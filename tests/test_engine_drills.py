"""Phase 2 drills against the simulated exchange (plan section 10, Phase 2)."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from marketbot.domain import Side
from marketbot.engine import Engine, Heartbeat
from marketbot.exchange.futures import STOP_MARKET, TAKE_PROFIT_MARKET, SymbolInfo
from marketbot.exchange.rest import BinanceAPIError
from marketbot.exchange.simulator import SimulatedExchange
from marketbot.risk.config import RiskConfig
from marketbot.risk.window import HaltReason, Mode
from marketbot.state.store import Store

pytestmark = pytest.mark.anyio
D = Decimal


def symbol(name: str) -> SymbolInfo:
    return SymbolInfo(
        symbol=name,
        tick_size=D("0.01"),
        step_size=D("0.01"),
        market_step_size=D("0.01"),
        min_qty=D("0.01"),
        market_max_qty=D(10000),
        min_notional=D(5),
        maintenance_margin_rate=D("0.005"),
    )


class Clock:
    def __init__(self) -> None:
        self.now = datetime.now(UTC)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **delta: float) -> None:
        self.now += timedelta(**delta)


class Rig:
    def __init__(self, tmp_path: Path, *, heartbeat: bool = False) -> None:
        self.exchange = SimulatedExchange(
            {name: symbol(name) for name in ("SOLUSDT", "ETHUSDT")},
            {"SOLUSDT": D(100), "ETHUSDT": D(3000)},
        )
        self.store = Store(tmp_path / "bot.db")
        self.clock = Clock()
        self.heartbeat = Heartbeat(tmp_path / "heartbeat") if heartbeat else None
        if self.heartbeat:
            self.heartbeat.touch()
        self.engine = self.restart()

    def restart(self) -> Engine:
        self.engine = Engine(
            self.exchange, self.store, RiskConfig(), heartbeat=self.heartbeat, clock=self.clock
        )
        return self.engine

    async def tick(self) -> bool:
        return await self.engine.tick()

    async def command(self, kind: str, **payload):
        command_id = self.store.enqueue(kind, payload)
        await self.tick()
        return self.store.command(command_id)

    def events(self) -> list[str]:
        return [event.kind for event in self.store.events(limit=1000)]

    def stops(self, name: str = "SOLUSDT"):
        return [
            order
            for order in self.exchange.conditional.values()
            if order.symbol == name and order.kind == STOP_MARKET
        ]


@pytest.fixture
async def rig(tmp_path: Path) -> Rig:
    rig = Rig(tmp_path)
    await rig.tick()
    return rig


async def open_long(rig: Rig, stop: str = "98", target: str | None = None):
    payload = {"symbol": "SOLUSDT", "side": "long", "stop": stop}
    if target:
        payload["target"] = target
    return await rig.command("propose", **payload)


async def test_entry_places_exchange_stop_and_target(rig: Rig) -> None:
    result = await open_long(rig, target="104")
    assert result.status == "done", result.result
    holding = rig.exchange.holdings["SOLUSDT"]
    assert holding.side is Side.LONG
    # 3% of E0 = 3 USDT at about 2.2 USDT risk per unit.
    assert holding.qty == D("1.36")
    kinds = sorted(order.kind for order in rig.exchange.conditional.values())
    assert kinds == [STOP_MARKET, TAKE_PROFIT_MARKET]
    assert rig.stops()[0].trigger_price == D(98)
    assert rig.engine.managed()["SOLUSDT"].stop_algo_id == rig.stops()[0].algo_id
    assert rig.engine.window.trades == 1


async def test_rejected_proposal_keeps_the_account_untouched(rig: Rig) -> None:
    result = await rig.command("propose", symbol="SOLUSDT", side="long", stop="101")
    assert result.status == "rejected"
    assert "losing side" in result.result["reasons"][0]
    assert not rig.exchange.holdings
    assert "decision" in rig.events()


async def test_drill_stop_rejected_closes_the_position(rig: Rig) -> None:
    rig.exchange.fail_next("place_conditional", BinanceAPIError(400, -2021, "would trigger"))
    result = await open_long(rig)
    assert result.status == "error"
    assert not rig.exchange.holdings
    assert {"entry_filled", "stop_failed", "position_closed"} <= set(rig.events())


async def test_drill_broken_fill_response_closes_the_fill_at_once(rig: Rig) -> None:
    # Found on Demo Trading: the order filled, but its response could not be parsed.
    rig.exchange.fail_next("market_after_fill", KeyError("avgPrice"))
    result = await open_long(rig)
    assert result.status == "error"
    assert not rig.exchange.holdings  # closed in the same tick, not left without a stop
    assert {"entry_uncertain", "position_closed"} <= set(rig.events())


async def test_stop_placement_survives_one_network_error(rig: Rig) -> None:
    rig.exchange.fail_next("place_conditional")
    result = await open_long(rig)
    assert result.status == "done"
    assert len(rig.stops()) == 1


async def test_drill_loss_line_gap_halts_the_window(rig: Rig) -> None:
    await open_long(rig)
    rig.exchange.set_mark("SOLUSDT", D(90))  # gaps through the 98 stop
    assert not rig.exchange.holdings
    await rig.tick()
    window = rig.engine.window
    assert window.mode is Mode.HALTED
    assert window.halt_reason is HaltReason.LOSS_LINE
    result = await open_long(rig, stop="88")
    assert result.status == "rejected"


async def test_drill_loss_line_with_open_position_flattens(rig: Rig) -> None:
    await open_long(rig)
    rig.exchange.wallet -= D(12)  # equity drops through the loss line elsewhere
    await rig.tick()
    assert not rig.exchange.holdings
    assert not rig.exchange.conditional
    assert rig.engine.window.mode is Mode.DEFENSE
    await rig.tick()
    assert rig.engine.window.mode is Mode.HALTED
    assert not rig.store.positions()


async def test_drill_target_protects_then_halts_at_the_lock_floor(rig: Rig) -> None:
    await open_long(rig)
    rig.exchange.set_mark("SOLUSDT", D(108))
    await rig.tick()
    window = rig.engine.window
    assert window.mode is Mode.PROTECT
    floor = window.lock_floor
    assert floor == D(109)
    stop = rig.stops()[0].trigger_price
    assert stop > D(106)  # moved up so that a stop-out keeps equity above the floor
    assert len(rig.stops()) == 1
    rig.exchange.set_mark("SOLUSDT", stop - D("0.01"))
    await rig.tick()
    # The tightened stop kept the gain above the floor, so the window stays in PROTECT.
    assert not rig.exchange.holdings
    equity = D(rig.store.get("status")["account"]["equity"])
    assert equity >= floor
    assert rig.engine.window.mode is Mode.PROTECT

    # New trades may risk only the cushion above the floor (here about 0.13 USDT): a wide
    # stop does not fit the minimum order size, a tight one does.
    wide = await open_long(rig, stop="104")
    assert wide.status == "rejected"
    assert "below the symbol minimum" in wide.result["reasons"][0]
    tight = await open_long(rig, stop="106.3")
    assert tight.status == "done", tight.result
    assert D(tight.result["risk"]) <= equity - floor

    rig.exchange.wallet -= equity - floor + D(1)  # equity falls through the floor
    await rig.tick()
    assert rig.engine.window.mode is Mode.HALTED
    assert rig.engine.window.halt_reason is HaltReason.LOCK_FLOOR
    assert not rig.exchange.holdings


async def test_drill_session_loss_flattens_and_stops(tmp_path: Path) -> None:
    rig = Rig(tmp_path, heartbeat=True)
    await rig.tick()
    await open_long(rig)
    rig.clock.advance(seconds=121)
    rig.exchange.fail_next("market")  # the first close attempt hits a network error
    assert await rig.tick() is False
    assert not rig.exchange.holdings
    assert not rig.exchange.conditional
    assert "session_lost" in rig.events()


async def test_engine_refuses_to_trade_without_a_heartbeat(tmp_path: Path) -> None:
    rig = Rig(tmp_path, heartbeat=True)
    rig.heartbeat.path.unlink()
    assert await rig.tick() is False
    assert "session_lost" in rig.events()


async def test_drill_network_loss_leaves_stops_in_place(rig: Rig) -> None:
    await open_long(rig)
    rig.exchange.fail_next("account")
    assert await rig.tick() is True
    assert rig.store.get("status")["error"].startswith("account unavailable")
    assert len(rig.stops()) == 1
    assert "SOLUSDT" in rig.exchange.holdings


async def test_drill_restart_restores_a_missing_stop(rig: Rig) -> None:
    await open_long(rig)
    rig.exchange.conditional.clear()  # the stop vanished while the bot was down
    rig.restart()
    await rig.tick()
    assert [s.trigger_price for s in rig.stops()] == [D(98)]
    assert "stop_missing" in rig.events()


async def test_restart_closes_position_whose_stop_cannot_be_restored(rig: Rig) -> None:
    await open_long(rig)
    rig.exchange.conditional.clear()
    rig.exchange.set_mark("SOLUSDT", D("97.5"))  # already below the stop
    rig.restart()
    await rig.tick()
    assert not rig.exchange.holdings


async def test_drill_reconciliation_mismatch(rig: Rig) -> None:
    await open_long(rig)
    rig.exchange.open_foreign_position("ETHUSDT", Side.SHORT, D("0.01"))
    await rig.exchange.place_conditional("ETHUSDT", "BUY", STOP_MARKET, D(3100), "stray")
    await rig.tick()
    assert "ETHUSDT" not in rig.exchange.holdings
    assert not [o for o in rig.exchange.conditional.values() if o.symbol == "ETHUSDT"]
    assert "reconcile_unknown_position" in rig.events()
    assert "SOLUSDT" in rig.exchange.holdings  # the managed position is untouched


async def test_extra_stops_are_cancelled(rig: Rig) -> None:
    await open_long(rig)
    await rig.exchange.place_conditional("SOLUSDT", "SELL", STOP_MARKET, D(95), "extra")
    await rig.tick()
    assert [s.trigger_price for s in rig.stops()] == [D(98)]


async def test_position_closed_by_stop_is_forgotten(rig: Rig) -> None:
    await open_long(rig)
    rig.exchange.set_mark("SOLUSDT", D("97.9"))
    await rig.tick()
    assert not rig.store.positions()
    assert "position_gone" in rig.events()


async def test_stop_command_flattens_and_stops(rig: Rig) -> None:
    await open_long(rig)
    command_id = rig.store.enqueue("stop", {"reason": "session stopped"})
    assert await rig.tick() is False
    assert rig.store.command(command_id).status == "done"
    assert not rig.exchange.holdings


async def test_pause_and_risk_scale(rig: Rig) -> None:
    await rig.command("pause")
    assert (await open_long(rig)).status == "rejected"
    await rig.command("resume")
    assert (await rig.command("risk_scale", scale="1.5")).status == "rejected"
    await rig.command("risk_scale", scale="0.5")
    assert (await open_long(rig)).status == "done"
    assert rig.exchange.holdings["SOLUSDT"].qty == D("0.68")


async def test_project_pause_flattens_and_blocks_entries(rig: Rig) -> None:
    await open_long(rig)
    rig.exchange.wallet -= D(35)
    await rig.tick()
    assert rig.engine.project.paused
    assert not rig.exchange.holdings
    result = await open_long(rig)
    assert result.status == "rejected"


async def test_window_rolls_over_with_new_e0(rig: Rig) -> None:
    rig.exchange.wallet = D(105)
    rig.clock.advance(hours=24)
    await rig.tick()
    window = rig.engine.window
    assert window.window_id == 2
    assert window.e0 == D(105)
    assert "window_closed" in rig.events()


async def test_moving_a_close_position_stop_uses_a_reduce_only_one(rig: Rig) -> None:
    # Found on Demo Trading: Binance rejects a second close-position stop (-4130), so a
    # stop placed by an older version must still be movable.
    await open_long(rig)
    for stop in rig.stops():
        await rig.exchange.cancel_conditional("SOLUSDT", stop.algo_id)
    legacy = await rig.exchange.place_conditional("SOLUSDT", "SELL", STOP_MARKET, D(98), "old")
    with pytest.raises(BinanceAPIError):
        await rig.exchange.place_conditional("SOLUSDT", "SELL", STOP_MARKET, D(97), "again")
    await rig.tick()  # adopts the legacy stop
    rig.exchange.set_mark("SOLUSDT", D(108))
    await rig.tick()
    [stop] = rig.stops()
    assert stop.algo_id != legacy.algo_id
    assert stop.reduce_only
    assert stop.qty == rig.exchange.holdings["SOLUSDT"].qty
    assert stop.trigger_price > D(106)


async def test_orphan_reduce_only_stop_is_cancelled_after_target(rig: Rig) -> None:
    await open_long(rig, target="104")
    rig.exchange.set_mark("SOLUSDT", D(104))  # the target closes the position
    assert not rig.exchange.holdings
    assert len(rig.stops()) == 1  # the reduce-only stop stays behind on the exchange
    await rig.tick()
    assert not rig.exchange.conditional
    assert "orphan_orders_cancelled" in rig.events()


async def test_keep_mode_leaves_protected_positions_on_session_loss(tmp_path: Path) -> None:
    rig = Rig(tmp_path, heartbeat=True)
    rig.engine.keep_positions_on_stop = True
    await rig.tick()
    await open_long(rig)
    rig.exchange.conditional.clear()  # the stop vanished just before the session ended
    rig.clock.advance(seconds=121)
    assert await rig.tick() is False
    assert "SOLUSDT" in rig.exchange.holdings
    assert [s.trigger_price for s in rig.stops()] == [D(98)]
    assert {"session_lost", "stop_missing", "positions_kept"} <= set(rig.events())


async def test_keep_mode_still_flattens_when_the_window_is_halted(tmp_path: Path) -> None:
    rig = Rig(tmp_path, heartbeat=True)
    rig.engine.keep_positions_on_stop = True
    await rig.tick()
    await open_long(rig)
    rig.exchange.wallet -= D(12)
    await rig.tick()  # DEFENSE closes everything anyway
    rig.clock.advance(seconds=121)
    assert await rig.tick() is False
    assert not rig.exchange.holdings
