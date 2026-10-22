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

# Telegram's command menu: every allowed command, so that typing "/" offers them all with a
# description. Telegram allows only [a-z0-9_] in command names: "risk-scale" is /risk_scale.
# A command acts on the live bot, or on Demo Trading with "demo" as its first argument.
MENU = {
    "status": "Equity, window, risk, positions",
    "summary": "Daily summary of both bots",
    "forward": "Out-of-time check per strategy",
    "report": "Trades and PnL, last 24 hours",
    "events": "Latest journal events",
    "ps": "What runs on the computer",
    "market": "Market brief from public data",
    "check": "API key and account checks",
    "strategies": "Show strategies; set a,b to choose",
    "pause": "Stop opening new positions",
    "resume": "Allow new positions again",
    "risk-scale": "Risk per trade multiplier, 0 to 1",
    "auto": "on|off: let strategies trade",
    "on-stop": "flatten|keep positions at session end",
    "restart": "Reload the bot, keep positions",
    "propose": "SYMBOL long|short --stop P: manual trade",
    "close": "SYMBOL: close one position (2 taps)",
    "flatten": "Close everything (2 taps)",
    "transfer": "AMOUNT to-futures|to-spot (2 taps)",
    "session": "start|stop the trading session",
    "help": "All commands; /help <command> for details",
}
# What a command runs without arguments, where that differs from the bare command.
DEFAULTS = {"strategies": "strategies show", "events": "events --limit 15"}


def telegram_name(command: str) -> str:
    return command.replace("-", "_")


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
    name = topic.split()[0].replace("_", "-")
    if name in DETAILS:
        return DETAILS[name]
    return f"No help for '{name}'. Commands: {', '.join(sorted(DETAILS))}."


def command_text(text: str) -> str | None:
    """The `mb` command for a Telegram message ("/status demo" -> "mb --env demo status",
    "/risk_scale 0.3" -> "mb risk-scale 0.3"), or None if it names no allowed command.
    "/mb ..." is accepted too, as Claude's proposals use it; "/help" is handled apart."""
    words = text.strip().split()
    if not words or not words[0].startswith("/"):
        return None
    name = words[0][1:].split("@")[0].lower().replace("_", "-")  # "/status@SomeBot" in groups
    rest = words[1:]
    if name == "mb":
        return "mb " + " ".join(rest)
    if name not in ALLOWED:
        return None
    env = ""
    if rest[:1] == ["demo"]:
        env, rest = "--env demo ", rest[1:]
    command = f"{name} {' '.join(rest)}" if rest else DEFAULTS.get(name, name)
    return f"mb {env}{command}"


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

    def typed(self) -> str:
        """As the owner types it in Telegram: mb --env demo risk-scale 0.3 is
        /risk_scale demo 0.3."""
        words = list(self.args)
        if words[:1] == ["--env"]:
            words = words[2:]
        demo = " demo" if self.env == "demo" else ""
        rest = shlex.join(words[1:])
        return f"/{telegram_name(words[0])}{demo}" + (f" {rest}" if rest else "")


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
