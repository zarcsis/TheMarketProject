"""Trading sessions: the detached bot process and the heartbeat that ties it to a session.

`mb session start` spawns the bot as a detached process, then keeps touching the heartbeat
file while the processes that started it are alive. When the Claude Code session ends,
those processes exit, the heartbeat stops, and the bot closes everything and exits on its
own. The bot is detached so that it survives the session long enough to clean up.
"""

import os
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import psutil

from marketbot.engine import Heartbeat

HEARTBEAT_INTERVAL_S = 15.0
# Long-lived system processes that do not mark the end of a session.
SYSTEM_PROCESSES = frozenset(
    {
        "system",
        "system idle process",
        "smss.exe",
        "csrss.exe",
        "wininit.exe",
        "winlogon.exe",
        "services.exe",
        "svchost.exe",
        "explorer.exe",
        "userinit.exe",
        "launchd",
        "init",
        "systemd",
    }
)


def session_ancestors() -> list[psutil.Process]:
    """Processes above this one whose exit means the session has ended."""
    return [
        process
        for process in psutil.Process().parents()
        if process.pid > 4 and process.name().lower() not in SYSTEM_PROCESSES
    ]


def bot_running(pid_file: Path) -> psutil.Process | None:
    try:
        pid = int(pid_file.read_text())
    except (FileNotFoundError, ValueError):
        return None
    try:
        process = psutil.Process(pid)
        cmdline = " ".join(process.cmdline())
    except psutil.Error:
        return None
    return process if "marketbot" in cmdline and " bot" in cmdline else None


def spawn_bot(env: str, pid_file: Path, log_file: Path) -> int:
    """Start `python -m marketbot --env <env> bot` detached from this process tree."""
    return spawn_detached(
        [sys.executable, "-m", "marketbot", "--env", env, "bot"], pid_file, log_file
    )


def spawn_detached(command: list[str], pid_file: Path, log_file: Path) -> int:
    """Start `command` so that it is not a descendant of this process; return its pid.

    Claude Code stops a background task by killing its process tree, which it finds
    through parent links (not a job object; verified on Windows 11 on 2026-10-22). An
    intermediate process starts the command and exits at once, so the command has no
    living ancestor in that tree and survives to close positions.
    """
    log_file.parent.mkdir(parents=True, exist_ok=True)
    pid_file.parent.mkdir(parents=True, exist_ok=True)
    pid_file.unlink(missing_ok=True)
    intermediate = _popen_detached(
        [sys.executable, "-m", "marketbot.session", str(pid_file), str(log_file), *command],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    if intermediate.wait(timeout=30) != 0 or not pid_file.exists():
        raise RuntimeError("the intermediate launcher failed")
    return int(pid_file.read_text())


def _launch(pid_file: Path, log_file: Path, command: list[str]) -> None:
    """Runs in the intermediate process."""
    with log_file.open("ab") as log:
        process = _popen_detached(
            command, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT
        )
    pid_file.write_text(str(process.pid))


def _popen_detached(command: list[str], **kwargs: Any) -> subprocess.Popen:
    """Popen outside the caller's console, process group, and (when allowed) job object.

    uv's `mb.exe` launcher holds its children in a Windows job that is closed when the
    launcher exits (seen with `mb remote start`, which returns at once), killing everything
    started inside it. Breaking away from the job keeps the detached process alive.
    """
    if os.name != "nt":
        return subprocess.Popen(command, start_new_session=True, **kwargs)
    flags = _detached()["creationflags"]
    try:
        return subprocess.Popen(
            command, creationflags=flags | subprocess.CREATE_BREAKAWAY_FROM_JOB, **kwargs
        )
    except OSError:
        return subprocess.Popen(command, creationflags=flags, **kwargs)


def _detached() -> dict[str, Any]:
    if os.name == "nt":
        return {
            "creationflags": subprocess.DETACHED_PROCESS
            | subprocess.CREATE_NEW_PROCESS_GROUP
            | subprocess.CREATE_NO_WINDOW
        }
    return {"start_new_session": True}


def keep_alive(
    heartbeat: Heartbeat,
    bot: psutil.Process,
    *,
    ancestors: list[psutil.Process] | None = None,
    interval_s: float = HEARTBEAT_INTERVAL_S,
    echo: Callable[[str], object] = print,
) -> str:
    """Touch the heartbeat until the bot exits or the session's processes are gone."""
    watched = session_ancestors() if ancestors is None else ancestors
    echo(
        f"Heartbeat for bot pid {bot.pid}; watching {len(watched)} session processes: "
        + ", ".join(f"{p.name()}({p.pid})" for p in watched)
    )
    while True:
        if not bot.is_running():
            return "bot exited"
        gone = [process for process in watched if not process.is_running()]
        if gone:
            return f"session process exited: {gone[0].pid}"
        heartbeat.touch()
        time.sleep(interval_s)


if __name__ == "__main__":
    _launch(Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3:])
