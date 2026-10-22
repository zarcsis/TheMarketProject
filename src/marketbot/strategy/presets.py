"""Named strategy configurations the bot can run (`MB_STRATEGIES=name1,name2`).

Each preset corresponds to a variant in research/results/02-strategies.md; see
research/02-strategies.md for why it is here.
"""

from collections.abc import Callable
from typing import Any

from marketbot.backtest.signals import Donchian, Fade, OpeningRange
from marketbot.backtest.trades import ExitRules
from marketbot.strategy.delisting import (
    AIRDROP_CONFIG,
    MONITORING_CONFIG,
    DelistingShort,
    DelistingShortConfig,
)
from marketbot.strategy.listing import ListingShort, ListingShortConfig
from marketbot.strategy.runtime import Klines, SignalStrategy, SignalStrategyConfig, Strategy

PRESETS: dict[str, SignalStrategyConfig] = {
    # The only research 02 variant positive in both periods (weak: t below 1.3).
    "donchian24-vol3-trail-inplay": SignalStrategyConfig(
        signal=Donchian(n=24, volume_mult=3.0, stop_atr=1.5),
        exits=ExitRules(target_r=None, trail_atr=3.0, breakeven_r=1.0),
        universe="in play",
    ),
    "donchian24-trail-top": SignalStrategyConfig(
        signal=Donchian(n=24, volume_mult=1.5, stop_atr=1.5),
        exits=ExitRules(target_r=None, trail_atr=3.0, breakeven_r=1.0),
        universe="top volume",
    ),
    "fade3-1r-inplay": SignalStrategyConfig(
        signal=Fade(k_atr=3.0, volume_mult=3.0),
        exits=ExitRules(target_r=1.0),
        universe="in play",
    ),
    "orb4-2r-top": SignalStrategyConfig(
        signal=OpeningRange(range_hours=4, stop_at_mid=True),
        exits=ExitRules(target_r=2.0),
        universe="top volume",
    ),
}


# Research 03: short busy crypto listings the day after listing, 40% stop, 7-day hold.
LISTING_PRESETS: dict[str, ListingShortConfig] = {
    "listing-short-busy-7d": ListingShortConfig(),
}


# Research 04: short a perpetual 5 minutes after its delisting announcement, 20% stop, 4 hours.
# Research 07: short a perpetual 5 minutes after Binance adds the Monitoring Tag, 1 day.
# Research 09: short a listed perpetual after Binance announces an airdrop of its token,
# 40% stop, 7 days.
DELISTING_PRESETS: dict[str, DelistingShortConfig] = {
    "delisting-short-4h": DelistingShortConfig(),
    "monitoring-short-1d": MONITORING_CONFIG,
    "airdrop-short-7d": AIRDROP_CONFIG,
}


def build(
    names: str, klines: Klines, journal: Callable[..., Any], store: Any | None = None
) -> list[Strategy]:
    strategies: list[Strategy] = []
    for name in (part.strip() for part in names.split(",") if part.strip()):
        if name in PRESETS:
            strategies.append(SignalStrategy(PRESETS[name], klines, journal=journal))
        elif name in LISTING_PRESETS:
            strategies.append(
                ListingShort(klines, LISTING_PRESETS[name], journal=journal, store=store)
            )
        elif name in DELISTING_PRESETS:
            strategies.append(
                DelistingShort(klines, DELISTING_PRESETS[name], journal=journal, store=store)
            )
        else:
            known = ", ".join([*PRESETS, *LISTING_PRESETS, *DELISTING_PRESETS])
            raise ValueError(f"Unknown strategy preset {name!r}; known: {known}")
    return strategies


def names() -> list[str]:
    return [*PRESETS, *LISTING_PRESETS, *DELISTING_PRESETS]
