"""Bot state in SQLite: key-value state, managed positions, the command queue, the journal.

The running bot and the `mb` CLI share one database file. The CLI enqueues commands and
reads the status; only the bot changes trading state.
"""

import json
import sqlite3
import time
from dataclasses import asdict, dataclass, fields, is_dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS positions (symbol TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS commands (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    kind TEXT NOT NULL,
    payload TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    result TEXT,
    processed_at TEXT
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    kind TEXT NOT NULL,
    payload TEXT NOT NULL
);
"""


@dataclass(frozen=True)
class Command:
    id: int
    kind: str
    payload: dict[str, Any]
    status: str = "pending"
    result: dict[str, Any] | None = None


@dataclass(frozen=True)
class Event:
    id: int
    ts: datetime
    kind: str
    payload: dict[str, Any]


class Store:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._db = sqlite3.connect(path, timeout=10, isolation_level=None)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.executescript(SCHEMA)

    def close(self) -> None:
        self._db.close()

    # Key-value state.

    def get(self, key: str) -> Any:
        row = self._db.execute("SELECT value FROM kv WHERE key = ?", (key,)).fetchone()
        return json.loads(row[0]) if row else None

    def put(self, key: str, value: Any) -> None:
        self._db.execute(
            "INSERT INTO kv (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, dumps(value)),
        )

    # Managed positions.

    def positions(self) -> dict[str, dict[str, Any]]:
        rows = self._db.execute("SELECT symbol, value FROM positions").fetchall()
        return {symbol: json.loads(value) for symbol, value in rows}

    def put_position(self, symbol: str, value: Any) -> None:
        self._db.execute(
            "INSERT INTO positions (symbol, value) VALUES (?, ?) "
            "ON CONFLICT(symbol) DO UPDATE SET value = excluded.value",
            (symbol, dumps(value)),
        )

    def delete_position(self, symbol: str) -> None:
        self._db.execute("DELETE FROM positions WHERE symbol = ?", (symbol,))

    # Commands.

    def enqueue(self, kind: str, payload: dict[str, Any] | None = None) -> int:
        cursor = self._db.execute(
            "INSERT INTO commands (created_at, kind, payload) VALUES (?, ?, ?)",
            (_now(), kind, dumps(payload or {})),
        )
        assert cursor.lastrowid is not None
        return cursor.lastrowid

    def pending(self) -> list[Command]:
        rows = self._db.execute(
            "SELECT id, kind, payload FROM commands WHERE status = 'pending' ORDER BY id"
        ).fetchall()
        return [Command(id, kind, json.loads(payload)) for id, kind, payload in rows]

    def complete(self, command_id: int, status: str, result: dict[str, Any]) -> None:
        self._db.execute(
            "UPDATE commands SET status = ?, result = ?, processed_at = ? WHERE id = ?",
            (status, dumps(result), _now(), command_id),
        )

    def command(self, command_id: int) -> Command | None:
        row = self._db.execute(
            "SELECT id, kind, payload, status, result FROM commands WHERE id = ?", (command_id,)
        ).fetchone()
        if row is None:
            return None
        id, kind, payload, status, result = row
        return Command(
            id, kind, json.loads(payload), status, json.loads(result) if result else None
        )

    def wait(self, command_id: int, timeout_s: float, poll_s: float = 0.25) -> Command | None:
        """Wait until the bot processes a command; None on timeout."""
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            command = self.command(command_id)
            if command is not None and command.status != "pending":
                return command
            time.sleep(poll_s)
        return None

    # Journal.

    def log(self, kind: str, /, **payload: Any) -> int:
        cursor = self._db.execute(
            "INSERT INTO events (ts, kind, payload) VALUES (?, ?, ?)",
            (_now(), kind, dumps(payload)),
        )
        assert cursor.lastrowid is not None
        return cursor.lastrowid

    def events(self, after_id: int = 0, limit: int = 100) -> list[Event]:
        rows = self._db.execute(
            "SELECT id, ts, kind, payload FROM events WHERE id > ? ORDER BY id LIMIT ?",
            (after_id, limit),
        ).fetchall()
        return [
            Event(id, datetime.fromisoformat(ts), kind, json.loads(payload))
            for id, ts, kind, payload in rows
        ]

    def last_events(self, limit: int = 20) -> list[Event]:
        rows = self._db.execute(
            "SELECT id, ts, kind, payload FROM events ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [
            Event(id, datetime.fromisoformat(ts), kind, json.loads(payload))
            for id, ts, kind, payload in reversed(rows)
        ]


# JSON with Decimal, datetime, Enum, and dataclasses.


def dumps(value: Any) -> str:
    return json.dumps(value, default=_encode, ensure_ascii=False, sort_keys=True)


def _encode(value: Any) -> Any:
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value) and not isinstance(value, type):
        return asdict(value)
    if isinstance(value, (set, frozenset, tuple)):
        return list(value)
    raise TypeError(f"Cannot encode {type(value).__name__}")


def load_dataclass[T](cls: type[T], data: dict[str, Any]) -> T:
    """Rebuild a flat dataclass from `dumps` output using its field annotations."""
    values: dict[str, Any] = {}
    for item in fields(cls):  # type: ignore[arg-type]
        if item.name not in data:
            continue
        values[item.name] = _decode(item.type, data[item.name])
    return cls(**values)


def _decode(annotation: Any, value: Any) -> Any:
    if value is None:
        return None
    text = annotation if isinstance(annotation, str) else getattr(annotation, "__name__", "")
    kinds = str(annotation)
    if "Decimal" in kinds or text == "Decimal":
        return Decimal(value)
    if "datetime" in kinds:
        return datetime.fromisoformat(value)
    for enum in _enum_types(annotation):
        return enum(value)
    return value


def _enum_types(annotation: Any) -> list[type[Enum]]:
    args = getattr(annotation, "__args__", None) or (annotation,)
    return [arg for arg in args if isinstance(arg, type) and issubclass(arg, Enum)]


def _now() -> str:
    return datetime.now(UTC).isoformat()
