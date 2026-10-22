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
        "events",
        "report",
        "summary",
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
