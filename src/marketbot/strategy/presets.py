"""Named strategy configurations the bot can run (`MB_STRATEGIES=name1,name2`).

Each preset corresponds to a variant in research/results/02-strategies.md; see
research/02-strategies.md for why it is here.
"""

from collections.abc import Callable
from typing import Any

from marketbot.backtest.signals import Donchian, Fade, OpeningRange
from marketbot.backtest.trades import ExitRules
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


def build(names: str, klines: Klines, journal: Callable[..., Any]) -> list[Strategy]:
    strategies: list[Strategy] = []
    for name in (part.strip() for part in names.split(",") if part.strip()):
        if name in PRESETS:
            strategies.append(SignalStrategy(PRESETS[name], klines, journal=journal))
        elif name in LISTING_PRESETS:
            strategies.append(ListingShort(klines, LISTING_PRESETS[name], journal=journal))
        else:
            known = ", ".join([*PRESETS, *LISTING_PRESETS])
            raise ValueError(f"Unknown strategy preset {name!r}; known: {known}")
    return strategies
