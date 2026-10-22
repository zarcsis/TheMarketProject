from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import polars as pl
import pytest
from test_engine_drills import STOP_MARKET, Rig

from marketbot.backtest.signals import Donchian
from marketbot.backtest.trades import ExitRules
from marketbot.domain import Side
from marketbot.strategy.runtime import (
    Close,
    MoveStop,
    PositionView,
    Propose,
    SignalStrategyConfig,
    choose_universe,
    manage,
)

pytestmark = pytest.mark.anyio
D = Decimal


class Scripted:
    """A strategy that returns prepared actions once."""

    name = "scripted"

    def __init__(self, *actions) -> None:
        self.actions = list(actions)

    async def step(self, now, positions):
        actions, self.actions = self.actions, []
        return actions


async def rig_with(tmp_path: Path, strategy: Scripted, *, auto: bool = True) -> Rig:
    rig = Rig(tmp_path)
    rig.engine.strategies = [strategy]
    if auto:
        rig.store.enqueue("auto", {"on": True})
    await rig.tick()
    return rig


async def test_strategies_wait_for_auto_on(tmp_path: Path) -> None:
    strategy = Scripted(Propose("SOLUSDT", Side.LONG, D(98), None, "scripted", "test"))
    rig = await rig_with(tmp_path, strategy, auto=False)
    await rig.tick()
    assert not rig.exchange.holdings


async def test_strategy_entry_goes_through_the_risk_manager(tmp_path: Path) -> None:
    strategy = Scripted(Propose("SOLUSDT", Side.LONG, D(98), D(104), "scripted", "test"))
    rig = await rig_with(tmp_path, strategy)
    assert rig.exchange.holdings["SOLUSDT"].qty == D("1.36")
    managed = rig.engine.managed()["SOLUSDT"]
    assert (managed.strategy, managed.initial_stop) == ("scripted", D(98))


async def test_stops_only_tighten_and_crossed_stops_close(tmp_path: Path) -> None:
    strategy = Scripted(Propose("SOLUSDT", Side.LONG, D(98), None, "scripted", "test"))
    rig = await rig_with(tmp_path, strategy)
    strategy.actions = [MoveStop("SOLUSDT", D(97), "looser")]
    await rig.tick()
    assert [s.trigger_price for s in rig.stops()] == [D(98)]
    strategy.actions = [MoveStop("SOLUSDT", D(99), "trailing")]
    await rig.tick()
    assert [s.trigger_price for s in rig.stops()] == [D(99)]
    strategy.actions = [MoveStop("SOLUSDT", D(101), "break-even")]  # above the 100 mark
    await rig.tick()
    assert not rig.exchange.holdings


async def test_strategy_close(tmp_path: Path) -> None:
    strategy = Scripted(Propose("SOLUSDT", Side.LONG, D(98), None, "scripted", "test"))
    rig = await rig_with(tmp_path, strategy)
    strategy.actions = [Close("SOLUSDT", "day end")]
    await rig.tick()
    assert not rig.exchange.holdings
    await rig.tick()
    assert not [o for o in rig.exchange.conditional.values() if o.kind == STOP_MARKET]


def five_minute_bars(start: datetime, highs: list[float]) -> pl.DataFrame:
    n = len(highs)
    return pl.DataFrame(
        {
            "open_time": [start + timedelta(minutes=5 * i) for i in range(n)],
            "open": [h - 0.5 for h in highs],
            "high": highs,
            "low": [h - 1.0 for h in highs],
            "close": [h - 0.2 for h in highs],
            "quote_volume": [1.0] * n,
        }
    ).with_columns(pl.col("open_time").cast(pl.Datetime("ms", "UTC")))


def view(stop: str = "98") -> PositionView:
    return PositionView(
        "SOLUSDT",
        Side.LONG,
        D(100),
        D(stop),
        D(2),
        datetime(2026, 9, 1, 10, tzinfo=UTC),
        "scripted",
    )


def config(exits: ExitRules) -> SignalStrategyConfig:
    return SignalStrategyConfig(Donchian(), exits, "top volume")


def test_manage_break_even_and_day_end() -> None:
    bars = five_minute_bars(datetime(2026, 9, 1, 10, tzinfo=UTC), [100.5, 102.5])
    now = datetime(2026, 9, 1, 10, 10, tzinfo=UTC)
    exits = ExitRules(target_r=2, breakeven_r=1.0)
    assert manage(view(), bars, config(exits), now) == [MoveStop("SOLUSDT", D(100), "break-even")]
    assert manage(view("100"), bars, config(exits), now) == []
    late = datetime(2026, 9, 1, 23, 55, tzinfo=UTC)
    assert manage(view(), bars, config(exits), late) == [Close("SOLUSDT", "day end")]


def test_choose_universe_from_previous_day() -> None:
    daily = pl.DataFrame(
        {
            "symbol": ["A", "B", "C"],
            "day": [date(2026, 9, 1)] * 3,
            "open": [1.0, 1.0, 1.0],
            "close": [1.5, 0.95, 1.01],
            "quote_volume": [6e7, 9e9, 1e8],
        }
    )
    assert choose_universe(daily, "top volume", date(2026, 9, 2)) == ["A", "B", "C"]
    assert choose_universe(daily, "in play", date(2026, 9, 2))[0] == "A"
