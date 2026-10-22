"""Live versions of research 04, 07, and 09: short perpetuals after a Binance announcement.

Research 04: delisting announcements. Research 07: the Monitoring Tag (a delisting risk
flag). Research 09: an airdrop (HODLer Airdrops, Launchpool, Megadrop) of a token whose
perpetual already trades. Research 14: Bybit or OKX delisting their own perpetual of a token.
Each configuration names the announcements source and the title parser.

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

from marketbot.data import announcements, exchanges
from marketbot.domain import Side
from marketbot.strategy.runtime import Action, Close, Klines, Memory, PositionView, Propose

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
    # Skip perpetuals with less trading history than this; None: no minimum.
    min_history: timedelta | None = None
    # Another announcements source than the Binance catalog, as an async function returning
    # `title` and `released`.
    fetch: Callable[[], Awaitable[pl.DataFrame]] | None = None
    # Skip perpetuals that Binance has itself scheduled for delisting.
    skip_scheduled: bool = False


DEFAULT_CONFIG = DelistingShortConfig()
MONITORING_CONFIG = DelistingShortConfig(
    hold=timedelta(days=1),
    name="monitoring-short",
    bucket="monitoring",
    catalog=announcements.NEWS,
    parser=announcements.monitoring_tagged_perpetuals,
)
# Research 09 tested entries up to 4 hours after the announcement, on perpetuals that had
# traded for at least a day; newer listings belong to the listing short.
AIRDROP_CONFIG = DelistingShortConfig(
    stop=Decimal("0.40"),
    hold=timedelta(days=7),
    max_delay=timedelta(hours=4),
    name="airdrop-short",
    bucket="airdrop",
    catalog=announcements.NEW_LISTINGS,
    parser=announcements.airdropped_perpetuals,
    min_history=timedelta(days=1),
)
# Research 14: the middle of the tested grid (holds 1-14 days, stops 20-40%), not its best;
# entries up to 1 hour after the announcement were tested.
EXCHANGE_DELISTING_CONFIG = DelistingShortConfig(
    stop=Decimal("0.30"),
    hold=timedelta(days=7),
    max_delay=timedelta(hours=1),
    name="exchange-delisting-short",
    bucket="exchange-delisting",
    parser=exchanges.perp_delistings,
    fetch=exchanges.latest_delistings,
    skip_scheduled=True,
)

Fetch = Callable[[], Awaitable[pl.DataFrame]]
# exchangeInfo's deliveryDate of a perpetual without a scheduled delisting (2100-12-25).
NO_DELIVERY = 4133404800000
# The announcements feed is a public website endpoint: a short outage is normal, a long one
# means the strategy is blind, which the owner must hear about.
DOWN_AFTER = timedelta(minutes=10)


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
        store: Any | None = None,
    ) -> None:
        self.config = config
        self.name = config.name
        self._klines = klines
        self._fetch = fetch or config.fetch or latest_from(config.catalog)
        self._journal = journal
        self._last_poll: datetime | None = None
        self._pending: dict[str, datetime] = {}  # symbol -> announcement time
        self._failing_since: datetime | None = None
        self._down_reported = False
        # Events already handled (entered or skipped), kept across restarts.
        self._done = Memory(store, f"strategy:{config.name}:done")

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
            self._done.add(f"{symbol}@{released.isoformat()}")
            if age > self.config.max_delay or symbol in positions:
                self._journal("delisting_skipped", symbol=symbol, age_s=int(age.total_seconds()))
                continue
            tradable = set(await self._klines.perpetuals())
            if symbol not in tradable:
                self._journal("delisting_skipped", symbol=symbol, reason="no tradable perpetual")
                continue
            if self.config.skip_scheduled and await self._scheduled(symbol):
                self._journal("delisting_skipped", symbol=symbol, reason="Binance delists it")
                continue
            if self.config.min_history is not None:
                hours = int(self.config.min_history / timedelta(hours=1))
                history = await self._klines.get(symbol, "1h", hours + 1)
                if history.height <= hours:
                    self._journal("delisting_skipped", symbol=symbol, reason="perpetual too new")
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

    async def _scheduled(self, symbol: str) -> bool:
        """Whether Binance has set a delivery (delisting) date for this perpetual."""
        info = await self._klines.exchange_info()
        row = next((r for r in info["symbols"] if r["symbol"] == symbol), None)
        return row is not None and row.get("deliveryDate", NO_DELIVERY) < NO_DELIVERY

    async def _poll(self, now: datetime) -> None:
        try:
            latest = await self._fetch()
        except Exception as exc:  # the website endpoint is best effort
            if self._failing_since is None:
                self._failing_since = now
                self._journal("announcements_error", strategy=self.name, error=repr(exc))
            elif not self._down_reported and now - self._failing_since >= DOWN_AFTER:
                self._down_reported = True
                self._journal(
                    "announcements_down",
                    strategy=self.name,
                    since=self._failing_since.isoformat(),
                    error=repr(exc),
                )
            return
        if self._down_reported:
            self._journal("announcements_restored", strategy=self.name)
        self._failing_since, self._down_reported = None, False
        recent = latest.filter(pl.col("released") >= now - self.config.max_delay)
        for row in recent.iter_rows(named=True):
            released = row["released"].astimezone(UTC)
            for symbol in self.config.parser(row["title"]):
                if (
                    f"{symbol}@{released.isoformat()}" not in self._done
                    and symbol not in self._pending
                ):
                    self._pending[symbol] = released
                    self._journal(
                        "delisting_announced",
                        strategy=self.name,
                        symbol=symbol,
                        title=row["title"],
                    )
