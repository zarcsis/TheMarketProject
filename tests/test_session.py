from datetime import UTC, datetime, timedelta
from pathlib import Path

from marketbot.engine import Heartbeat
from marketbot.session import keep_alive


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
