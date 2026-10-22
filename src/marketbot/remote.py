"""Remote control: which `mb` commands may run from the Telegram remote, and proposals.

The Telegram remote runs `mb` commands for the account owner: typed by the owner, or
proposed by Claude (`mb ask`) and run when the owner taps "Run". Only the `mb` program
runs, never a shell. Proposals and their outcomes live in `state/remote.db`.
"""

import shlex
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from marketbot.state.store import Store

STORE_PATH = Path("state") / "remote.db"

# Commands the remote may run. Research, data downloads, key generation, and the internal
# bot process are left to the computer.
ALLOWED = frozenset(
    {
        "status",
        "ps",
        "events",
        "report",
        "summary",
        "forward",
        "market",
        "check",
        "pause",
        "resume",
        "risk-scale",
        "auto",
        "propose",
        "close",
        "flatten",
        "restart",
        "strategies",
        "on-stop",
        "transfer",
        "session",
    }
)
SESSION_ACTIONS = frozenset({"start", "stop"})
# These need a second tap: they close positions or move money.
DANGEROUS = frozenset({"flatten", "transfer", "session stop", "close"})

# Short commands for Telegram's command menu (names: lowercase letters only), so that typing
# "/" offers them with a description. Each runs the given `mb` command on the live bot, or on
# Demo Trading with "demo" after it: "/status demo".
SHORTCUTS = {
    "status": ("status", "Equity, window, risk, positions"),
    "summary": ("summary", "Daily summary of both bots"),
    "forward": ("forward", "Out-of-time check per strategy"),
    "report": ("report", "Trades and PnL, last 24 hours"),
    "events": ("events --limit 15", "Latest journal events"),
    "ps": ("ps", "What runs on the computer"),
    "market": ("market", "Market brief from public data"),
    "check": ("check", "API key and account checks"),
    "strategies": ("strategies show", "Configured and available strategies"),
    "pause": ("pause", "Stop opening new positions"),
    "resume": ("resume", "Allow new positions again"),
    "help": ("", "All commands; /help <command> for details"),
}

_HELP_FILE = Path(__file__).with_name("remote_help.txt")


def _help_sections() -> dict[str, str]:
    """`remote_help.txt`: sections headed "## name"; "overview" is the /help text."""
    sections: dict[str, list[str]] = {}
    current = None
    for line in _HELP_FILE.read_text(encoding="utf-8").splitlines():
        if line.startswith("## "):
            current = line[3:].strip()
            sections[current] = []
        elif current is not None:
            sections[current].append(line)
    return {name: "\n".join(lines).strip() for name, lines in sections.items()}


_SECTIONS = _help_sections()
HELP = _SECTIONS.pop("overview")
DETAILS = _SECTIONS


def help_text(topic: str = "") -> str:
    """The overview, or one command's details ("/help propose", "/help risk-scale")."""
    topic = topic.strip().lstrip("/").removeprefix("mb ").strip()
    if not topic:
        return HELP
    name = topic.split()[0]
    if name in DETAILS:
        return DETAILS[name]
    return f"No help for '{name}'. Commands: {', '.join(sorted(DETAILS))}."


def shortcut(text: str) -> str | None:
    """`mb` command text for a short command ("/status demo" -> "mb --env demo status"),
    or None if the text is not one. "/help" is handled apart."""
    words = text.strip().split()
    if not words or not words[0].startswith("/"):
        return None
    name = words[0][1:].split("@")[0].lower()  # "/status@SomeBot" in group chats
    if name not in SHORTCUTS or name == "help":
        return None
    rest = words[1:]
    env = ""
    if rest[:1] == ["demo"]:
        env, rest = "--env demo ", rest[1:]
    command = SHORTCUTS[name][0]
    if rest and command.startswith("events "):
        command = "events"  # explicit options replace the default limit
    return f"mb {env}{command} {' '.join(rest)}".strip()


class RemoteCommandError(ValueError):
    pass


@dataclass(frozen=True)
class RemoteCommand:
    args: tuple[str, ...]  # arguments after `mb`
    env: str
    name: str  # the subcommand, e.g. "status" or "session stop"

    @property
    def dangerous(self) -> bool:
        return self.name in DANGEROUS or self.name.split()[0] in DANGEROUS

    @property
    def blocking(self) -> bool:
        """`mb session start` runs until the session ends, so it is started, not awaited."""
        return self.name == "session start"

    def text(self) -> str:
        return "mb " + shlex.join(self.args)


def parse(text: str) -> RemoteCommand:
    try:
        words = shlex.split(text.strip())
    except ValueError as exc:
        raise RemoteCommandError(f"cannot parse: {exc}") from exc
    if words and words[0] in ("mb", "/mb"):
        words = words[1:]
    env = "live"
    rest = list(words)
    if rest[:1] == ["--env"]:
        if len(rest) < 2 or rest[1] not in ("live", "demo"):
            raise RemoteCommandError("--env must be live or demo")
        env, rest = rest[1], rest[2:]
    if not rest:
        raise RemoteCommandError("no command")
    command = rest[0]
    if command not in ALLOWED:
        raise RemoteCommandError(
            f"'{command}' is not allowed from the remote; allowed: {', '.join(sorted(ALLOWED))}"
        )
    name = command
    if command == "session":
        if len(rest) < 2 or rest[1] not in SESSION_ACTIONS:
            raise RemoteCommandError("session needs start or stop")
        name = f"session {rest[1]}"
    return RemoteCommand(tuple(words), env, name)


# Proposals.


def store(path: Path = STORE_PATH) -> Store:
    return Store(path)


def propose(text: str, why: str, ttl: timedelta, path: Path = STORE_PATH) -> int:
    """Queue a proposal for the owner; returns its id. Raises on a disallowed command."""
    command = parse(text)
    expires = datetime.now(UTC) + ttl
    return store(path).enqueue(
        "proposal", {"command": command.text(), "why": why, "expires": expires.isoformat()}
    )


def expired(payload: dict[str, Any], now: datetime | None = None) -> bool:
    return (now or datetime.now(UTC)) >= datetime.fromisoformat(payload["expires"])
