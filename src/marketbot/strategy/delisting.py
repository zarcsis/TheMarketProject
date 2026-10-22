"""Live versions of research 04 and 07: short perpetuals after a Binance announcement.

Research 04: delisting announcements. Research 07: the Monitoring Tag (a delisting risk
flag). Each configuration names the announcements catalog and the title parser.

Backtest: entry at the open of the first 5-minute bar at least 5 minutes after the
announcement, stop 20% above entry, close after 4 hours. Live: the strategy polls the
announcements catalog, and enters an announced symbol once 5 minutes have passed and at
most `max_delay` after the announcement; later it skips the event.
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import httpx
import polars as pl

from marketbot.data import announcements
from marketbot.domain import Side
from marketbot.strategy.runtime import Action, Close, Klines, PositionView, Propose

NAME = "delisting-short"
BUCKET = "delisting"


@dataclass(frozen=True)
class DelistingShortConfig:
    stop: Decimal = Decimal("0.20")
    hold: timedelta = timedelta(hours=4)
    delay: timedelta = timedelta(minutes=5)
    max_delay: timedelta = timedelta(minutes=30)
    poll: timedelta = timedelta(minutes=1)
    name: str = NAME
    bucket: str = BUCKET
    catalog: int = announcements.DELISTINGS
    parser: Callable[[str], list[str]] = announcements.delisted_perpetuals


DEFAULT_CONFIG = DelistingShortConfig()
MONITORING_CONFIG = DelistingShortConfig(
    hold=timedelta(days=1),
    name="monitoring-short",
    bucket="monitoring",
    catalog=announcements.NEWS,
    parser=announcements.monitoring_tagged_perpetuals,
)

Fetch = Callable[[], Awaitable[pl.DataFrame]]


def latest_from(catalog: int) -> Fetch:
    async def fetch() -> pl.DataFrame:
        async with httpx.AsyncClient(timeout=20) as client:
            return await announcements.fetch_catalog(client, catalog, max_pages=1)

    return fetch


class DelistingShort:
    def __init__(
        self,
        klines: Klines,
        config: DelistingShortConfig = DEFAULT_CONFIG,
        *,
        fetch: Fetch | None = None,
        journal: Callable[..., Any] = lambda *a, **k: None,
    ) -> None:
        self.config = config
        self.name = config.name
        self._klines = klines
        self._fetch = fetch or latest_from(config.catalog)
        self._journal = journal
        self._last_poll: datetime | None = None
        self._pending: dict[str, datetime] = {}  # symbol -> announcement time
        self._done: set[tuple[str, datetime]] = set()

    async def step(self, now: datetime, positions: dict[str, PositionView]) -> list[Action]:
        actions: list[Action] = [
            Close(symbol, f"held {self.config.hold}")
            for symbol, position in positions.items()
            if position.strategy == self.name and now - position.opened_at >= self.config.hold
        ]
        if self._last_poll is None or now - self._last_poll >= self.config.poll:
            self._last_poll = now
            await self._poll(now)
        due = [s for s, released in self._pending.items() if now - released >= self.config.delay]
        if len(due) > 1:
            # The risk rules allow few positions per bucket: take the most liquid first.
            try:
                volumes = await self._klines.quote_volumes()
            except Exception:  # ordering is best effort
                volumes = {}
            due.sort(key=lambda s: volumes.get(s, 0.0), reverse=True)
        ordered = [(s, self._pending[s]) for s in due]
        for symbol, released in ordered:
            age = now - released
            if age < self.config.delay:
                continue
            del self._pending[symbol]
            self._done.add((symbol, released))
            if age > self.config.max_delay or symbol in positions:
                self._journal("delisting_skipped", symbol=symbol, age_s=int(age.total_seconds()))
                continue
            tradable = set(await self._klines.perpetuals())
            if symbol not in tradable:
                self._journal("delisting_skipped", symbol=symbol, reason="no tradable perpetual")
                continue
            price = Decimal(str((await self._klines.get(symbol, "1m", 1))["close"][0]))
            actions.append(
                Propose(
                    symbol=symbol,
                    side=Side.SHORT,
                    stop=price * (1 + self.config.stop),
                    target=None,
                    strategy=self.name,
                    why=f"{self.name}: announced {released:%Y-%m-%d %H:%M:%S} UTC",
                    bucket=self.config.bucket,
                )
            )
        return actions

    async def _poll(self, now: datetime) -> None:
        try:
            latest = await self._fetch()
        except Exception as exc:  # the website endpoint is best effort
            self._journal("announcements_error", error=repr(exc))
            return
        recent = latest.filter(pl.col("released") >= now - self.config.max_delay)
        for row in recent.iter_rows(named=True):
            released = row["released"].astimezone(UTC)
            for symbol in self.config.parser(row["title"]):
                if (symbol, released) not in self._done and symbol not in self._pending:
                    self._pending[symbol] = released
                    self._journal("delisting_announced", symbol=symbol, title=row["title"])
