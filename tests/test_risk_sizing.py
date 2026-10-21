from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from marketbot.risk.config import RiskConfig
from marketbot.risk.sizing import (
    OpenPosition,
    Side,
    SymbolRules,
    TradeIntent,
    entry_floor,
    position_risk,
    size_trade,
    tighten_stops,
    total_risk,
)
from marketbot.risk.window import Mode, WindowState, open_window

CONFIG = RiskConfig()
D = Decimal
WINDOW = open_window(1, D(100), datetime(2026, 10, 22, tzinfo=UTC))
RULES = SymbolRules(step_size=D("0.001"), min_qty=D("0.001"), min_notional=D(5))
LONG = TradeIntent("SOLUSDT", Side.LONG, entry=D(100), stop=D(98))
HUNDRED = D(100)


def size(
    intent=LONG, state=WINDOW, equity=HUNDRED, positions=(), rules=RULES, margin=HUNDRED, **kw
):
    return size_trade(intent, state, equity, positions, rules, margin, CONFIG, **kw)


def test_sizes_to_three_percent_risk() -> None:
    decision = size()
    assert decision.approved
    # Unit risk: 2 + 100 * 0.0005 + 98 * 0.0015 = 2.197; 3 / 2.197 = 1.3654...
    assert decision.qty == D("1.365")
    assert decision.risk == D("1.365") * D("2.197")
    assert decision.leverage == 2


def test_short_mirrors_long() -> None:
    short = TradeIntent("SOLUSDT", Side.SHORT, entry=D(100), stop=D(102))
    assert size(short).approved


@pytest.mark.parametrize(
    ("intent", "reason"),
    [
        (replace(LONG, stop=D(101)), "losing side"),
        (replace(LONG, stop=D(100)), "losing side"),
    ],
)
def test_rejects_stop_on_wrong_side(intent, reason) -> None:
    decision = size(intent)
    assert not decision.approved
    assert reason in decision.reasons[0]


@pytest.mark.parametrize("mode", [Mode.DEFENSE, Mode.HALTED])
def test_no_entries_in_flat_modes(mode) -> None:
    assert not size(state=replace(WINDOW, mode=mode)).approved


def test_caution_halves_size_and_allows_one_position() -> None:
    caution = replace(WINDOW, mode=Mode.CAUTION)
    assert size(state=caution, equity=D(95)).qty == D("0.682")
    other = OpenPosition("ETHUSDT", Side.LONG, D("0.01"), D(3000), D(2950))
    assert not size(state=caution, equity=D(95), positions=[other]).approved


def test_budget_shrinks_near_the_loss_line() -> None:
    caution = replace(WINDOW, mode=Mode.CAUTION)
    decision = size(state=caution, equity=D("92"))
    # Floor is 91 + 0.5 = 91.5, so at most 0.5 of risk is left.
    assert decision.approved
    assert decision.risk <= D("0.5")
    assert not size(state=caution, equity=D("91.5")).approved


def test_protect_risks_only_the_cushion_above_the_lock_floor() -> None:
    protect = replace(WINDOW, mode=Mode.PROTECT, lock_floor=D(109))
    decision = size(state=protect, equity=D(110))
    assert decision.approved
    assert decision.risk <= D(1)


def test_rejects_when_minimum_notional_does_not_fit_the_budget() -> None:
    btc = SymbolRules(step_size=D("0.001"), min_qty=D("0.001"), min_notional=D(50))
    wide = TradeIntent("BTCUSDT", Side.LONG, entry=D(100000), stop=D(90000))
    decision = size(wide, rules=btc)
    assert not decision.approved
    assert "below the symbol minimum" in decision.reasons[0]


def test_portfolio_limits() -> None:
    eth = OpenPosition("ETHUSDT", Side.LONG, D("0.002"), D(3000), D(2950))
    xrp = OpenPosition("XRPUSDT", Side.LONG, D(3), D(2), D("1.95"))
    doge = OpenPosition("DOGEUSDT", Side.SHORT, D(30), D("0.2"), D("0.21"))
    assert "already open" in size(positions=[replace(eth, symbol="SOLUSDT")]).reasons[0]
    assert "bucket" in size(positions=[eth, xrp]).reasons[0]
    assert "limit is 3" in size(positions=[eth, xrp, doge]).reasons[0]
    assert not size(state=replace(WINDOW, trades=12)).approved


def test_effective_leverage_cap() -> None:
    tight = replace(LONG, stop=D("99.9"))
    decision = size(tight)
    assert decision.approved
    assert decision.qty * tight.entry <= CONFIG.leverage.max_effective * 100
    assert "effective leverage" in decision.reasons[0]


def test_unprotected_position_counts_full_notional() -> None:
    naked = OpenPosition("ETHUSDT", Side.LONG, D("0.01"), D(3000), None)
    assert position_risk(naked, CONFIG.costs) == D(30)


prices = st.decimals(min_value=D("0.01"), max_value=D(100000), places=2)


@st.composite
def scenarios(draw):
    entry = draw(prices)
    stop_fraction = draw(st.decimals(min_value=D("0.001"), max_value=D("0.3"), places=4))
    side = draw(st.sampled_from(Side))
    stop = entry * (1 - stop_fraction) if side is Side.LONG else entry * (1 + stop_fraction)
    intent = TradeIntent("NEWUSDT", side, entry, stop)
    mode = draw(st.sampled_from([Mode.NORMAL, Mode.CAUTION, Mode.PROTECT]))
    e0 = D(100)
    equity = draw(st.decimals(min_value=D(80), max_value=D(140), places=2))
    state = WindowState(1, WINDOW.opened_at, e0, max(e0, equity), min(e0, equity), mode=mode)
    if mode is Mode.PROTECT:
        state = replace(state, lock_floor=D(109))
    positions = []
    for index in range(draw(st.integers(0, 2))):
        mark = draw(prices)
        position_side = draw(st.sampled_from(Side))
        distance = draw(st.decimals(min_value=D("0.001"), max_value=D("0.2"), places=3))
        position_stop = mark * (1 - distance if position_side is Side.LONG else 1 + distance)
        qty = draw(st.decimals(min_value=D("0.001"), max_value=D(2), places=3))
        positions.append(OpenPosition(f"P{index}USDT", position_side, qty, mark, position_stop))
    margin = draw(st.decimals(min_value=D(1), max_value=D(140), places=2))
    scale = draw(st.decimals(min_value=D(0), max_value=D(1), places=2))
    return intent, state, equity, positions, margin, scale


@given(scenarios())
def test_approved_entries_never_breach_the_floor(scenario) -> None:
    intent, state, equity, positions, margin, scale = scenario
    decision = size_trade(intent, state, equity, positions, RULES, margin, CONFIG, risk_scale=scale)
    if not decision.approved:
        return
    floor = entry_floor(state, CONFIG)
    assert equity - total_risk(positions, CONFIG.costs) - decision.risk >= floor
    assert decision.qty % RULES.step_size == 0
    assert decision.qty * intent.entry >= RULES.min_notional
    open_notional = sum((p.notional for p in positions), D(0))
    assert open_notional + decision.qty * intent.entry <= CONFIG.leverage.max_effective * equity
    assert 1 <= decision.leverage <= CONFIG.leverage.max_exchange
    assert decision.qty * intent.entry <= margin * decision.leverage


@given(
    st.lists(
        st.tuples(
            st.sampled_from(Side),
            st.decimals(min_value=D(1), max_value=D(1000), places=2),
            st.decimals(min_value=D("0.01"), max_value=D("0.2"), places=3),
            st.decimals(min_value=D("0.01"), max_value=D(1), places=2),
        ),
        min_size=1,
        max_size=3,
    ),
    st.decimals(min_value=D(-5), max_value=D(30), places=2),
)
def test_tightened_stops_respect_the_floor(raw_positions, cushion) -> None:
    positions = [
        OpenPosition(
            f"S{index}USDT",
            side,
            qty,
            mark,
            mark * (1 - distance if side is Side.LONG else 1 + distance),
        )
        for index, (side, mark, distance, qty) in enumerate(raw_positions)
    ]
    equity, floor = D(110), D(110) - cushion
    changes = tighten_stops(positions, equity, floor, CONFIG.costs)
    if not changes:
        assert total_risk(positions, CONFIG.costs) <= cushion
        return
    if cushion <= 0:
        assert all(change.stop is None for change in changes)
        return
    # Positions whose new stop would cross the mark are closed instead.
    kept = [
        (position, replace(position, stop=change.stop))
        for position, change in zip(positions, changes, strict=True)
        if change.stop is not None
    ]
    for before, after in kept:
        assert after.stop > 0
        if before.side is Side.LONG:
            assert before.stop <= after.stop < before.mark
        else:
            assert before.mark < after.stop <= before.stop
    assert total_risk([after for _, after in kept], CONFIG.costs) <= cushion + D("1e-18")
