from datetime import UTC, datetime, timedelta
from pathlib import Path

from marketbot.engine import Heartbeat
from marketbot.session import keep_alive, mb_args, mb_label, mb_processes


class FakeProcess:
    def __init__(self, pid: int, lives: int) -> None:
        self.pid = pid
        self._lives = lives

    def name(self) -> str:
        return f"proc{self.pid}"

    def is_running(self) -> bool:
        self._lives -= 1
        return self._lives >= 0


def test_heartbeat_stops_when_a_session_process_exits(tmp_path: Path) -> None:
    heartbeat = Heartbeat(tmp_path / "heartbeat")
    bot = FakeProcess(1, lives=100)
    claude = FakeProcess(2, lives=3)
    reason = keep_alive(heartbeat, bot, ancestors=[claude], interval_s=0, echo=lambda _: None)
    assert reason == "session process exited: 2"
    assert heartbeat.alive(datetime.now(UTC))


def test_heartbeat_stops_when_the_bot_exits(tmp_path: Path) -> None:
    heartbeat = Heartbeat(tmp_path / "heartbeat")
    reason = keep_alive(
        heartbeat, FakeProcess(1, lives=2), ancestors=[], interval_s=0, echo=lambda _: None
    )
    assert reason == "bot exited"


def test_heartbeat_age(tmp_path: Path) -> None:
    heartbeat = Heartbeat(tmp_path / "heartbeat", timeout=timedelta(seconds=120))
    now = datetime.now(UTC)
    assert heartbeat.age(now) is None
    assert not heartbeat.alive(now)
    heartbeat.touch()
    assert heartbeat.alive(now)
    assert not heartbeat.alive(now + timedelta(seconds=121))


def test_mb_args_finds_module_and_script_launches() -> None:
    module = ["C:/venv/Scripts/python.exe", "-m", "marketbot", "--env", "live", "bot"]
    assert mb_args(module) == ["--env", "live", "bot"]
    script = ["C:/uv/python.exe", "C:/p/.venv/Scripts/mb.exe", "session", "start"]
    assert mb_args(script) == ["session", "start"]
    assert mb_args(["/usr/bin/python3", "/p/.venv/bin/mb", "ps"]) == ["ps"]
    assert mb_args(["python", "-m", "marketbot.session", "bot.pid", "bot.log"]) is None
    assert mb_args(["bash.exe", "-c", "echo mb"]) is None
    assert mb_args([]) is None


def test_mb_labels() -> None:
    assert mb_label(["--env", "live", "bot"]) == "live bot"
    assert mb_label(["session", "start"]) == "live session"
    assert mb_label(["--env", "demo", "session", "start"]) == "demo session"
    assert mb_label(["remote", "run"]) == "Telegram remote"
    assert mb_label(["--env", "demo", "status"]) == "mb --env demo status"


def test_mb_processes_lists_each_launcher_once_and_leaves_out_the_caller() -> None:
    demo_session = ["--env", "demo", "session", "start"]
    demo_bot = ["python.exe", "-m", "marketbot", "--env", "demo", "bot"]
    remote = ["pythonw.exe", "-m", "marketbot", "remote", "run"]
    ps = ["python.exe", "-m", "marketbot", "ps"]
    rows = [
        (10, 1, ["mb.exe", *demo_session], 100.0),
        (11, 10, ["python.exe", "mb.exe", *demo_session], 101.0),
        (12, 11, ["python.exe", "mb.exe", *demo_session], 102.0),
        (20, 1, demo_bot, 103.0),
        (21, 20, demo_bot, 103.5),
        (30, 1, remote, 90.0),
        (31, 30, remote, 90.5),
        (40, 31, ps, 200.0),  # the remote runs `mb ps`
        (41, 40, ps, 200.5),
        (50, 1, ["bash.exe"], 50.0),
    ]
    found = mb_processes(rows, own_pid=41)
    assert [(process.pid, process.label) for process in found] == [
        (30, "Telegram remote"),
        (10, "demo session"),
        (20, "demo bot"),
    ]
