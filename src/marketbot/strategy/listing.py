"""Live version of research 03: short busy crypto listings the day after listing.

Backtest: short at the open of the first full UTC day after the listing day, stop 40% above
entry, close after `hold_days`. Live, the entry is allowed during the first hours of that
day (the session may start after 00:00 UTC), which is a small deviation from the backtest.
Positions are held for days, so they outlive a single trading window.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

from marketbot.domain import Side
from marketbot.strategy.runtime import Action, Close, Klines, PositionView, Propose

NAME = "listing-short"
BUCKET = "listing"


@dataclass(frozen=True)
class ListingShortConfig:
    stop: Decimal = Decimal("0.40")
    hold_days: int = 7
    min_listing_volume: float = 50_000_000.0  # research 03 "busy" threshold
    last_entry_hour: int = 12  # UTC hour on day 1 after which a missed entry is skipped


DEFAULT_CONFIG = ListingShortConfig()


@dataclass(frozen=True)
class Listing:
    symbol: str
    listed: date  # UTC day of onboardDate


def recent_listings(exchange_info: dict, today: date) -> list[Listing]:
    """Crypto perpetuals listed yesterday (UTC)."""
    listings = []
    for row in exchange_info["symbols"]:
        if (
            row.get("contractType") != "PERPETUAL"
            or row.get("underlyingType") != "COIN"
            or row.get("quoteAsset") != "USDT"
            or row.get("status") != "TRADING"
        ):
            continue
        listed = datetime.fromtimestamp(row["onboardDate"] / 1000, UTC).date()
        if listed == today - timedelta(days=1):
            listings.append(Listing(row["symbol"], listed))
    return listings


class ListingShort:
    name = NAME

    def __init__(
        self,
        klines: Klines,
        config: ListingShortConfig = DEFAULT_CONFIG,
        *,
        journal: Callable[..., Any] = lambda *a, **k: None,
    ) -> None:
        self.config = config
        self._klines = klines
        self._journal = journal
        self._checked: set[str] = set()  # symbols already decided (entered or skipped)
        self._last_bar: datetime | None = None

    async def step(self, now: datetime, positions: dict[str, PositionView]) -> list[Action]:
        bar = now.replace(second=0, microsecond=0) - timedelta(minutes=now.minute % 5)
        if bar == self._last_bar:
            return []
        self._last_bar = bar
        actions: list[Action] = [
            Close(symbol, f"held {self.config.hold_days} days")
            for symbol, position in positions.items()
            if position.strategy == self.name
            and now - position.opened_at >= timedelta(days=self.config.hold_days)
        ]
        if now.hour >= self.config.last_entry_hour:
            return actions
        info = await self._klines.exchange_info()
        for listing in recent_listings(info, now.date()):
            if listing.symbol in self._checked or listing.symbol in positions:
                continue
            self._checked.add(listing.symbol)
            daily = await self._klines.get(listing.symbol, "1d", 3)
            day0 = daily.filter(daily["open_time"].dt.date() == listing.listed)
            volume = float(day0["quote_volume"][0]) if day0.height else 0.0
            if volume < self.config.min_listing_volume:
                self._journal(
                    "listing_skipped", symbol=listing.symbol, listing_volume=round(volume)
                )
                continue
            price = Decimal(str((await self._klines.get(listing.symbol, "1m", 1))["close"][0]))
            actions.append(
                Propose(
                    symbol=listing.symbol,
                    side=Side.SHORT,
                    stop=price * (1 + self.config.stop),
                    target=None,
                    strategy=self.name,
                    why=f"listed {listing.listed}, listing-day volume {volume / 1e6:.0f}M USDT",
                    bucket=BUCKET,
                )
            )
        return actions
