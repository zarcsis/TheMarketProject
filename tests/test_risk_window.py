from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from hypothesis import given
from hypothesis import strategies as st

from marketbot.risk.config import RiskConfig, load_risk_config
from marketbot.risk.window import (
    ENTRY_MODES,
    FLAT_MODES,
    HaltReason,
    Mode,
    WindowState,
    open_window,
    update_window,
)

CONFIG = RiskConfig()
START = datetime(2026, 10, 22, 8, tzinfo=UTC)
D = Decimal


def step(state: WindowState, equity: str, *, flat: bool = False, hours: float = 1):
    return update_window(state, D(equity), START + timedelta(hours=hours), CONFIG, flat=flat)


def test_committed_config_matches_defaults() -> None:
    root = Path(__file__).resolve().parents[1]
    assert load_risk_config(root / "config" / "risk.toml") == RiskConfig()


def test_target_switches_to_protect_with_lock_floor() -> None:
    state = open_window(1, D(100), START)
    update = step(state, "110")
    assert update.state.mode is Mode.PROTECT
    assert update.state.lock_floor == D("109.00")
    assert [t.reason for t in update.transitions] == ["target reached"]


def test_lock_floor_ratchets_and_halts() -> None:
    state = step(open_window(1, D(100), START), "110").state
    state = step(state, "120").state
    assert state.lock_floor == D("114.00")
    state = step(state, "116").state
    assert state.mode is Mode.PROTECT
    assert state.lock_floor == D("114.00")
    state = step(state, "114").state
    assert state.mode is Mode.HALTED
    assert state.halt_reason is HaltReason.LOCK_FLOOR


def test_caution_and_recovery() -> None:
    state = step(open_window(1, D(100), START), "95").state
    assert state.mode is Mode.CAUTION
    assert step(state, "96.9").state.mode is Mode.CAUTION
    assert step(state, "97").state.mode is Mode.NORMAL


def test_loss_line_defends_until_flat() -> None:
    state = step(open_window(1, D(100), START), "91").state
    assert state.mode is Mode.DEFENSE
    assert state.halt_reason is HaltReason.LOSS_LINE
    assert step(state, "90.5").state.mode is Mode.DEFENSE
    assert step(state, "90.5", flat=True).state.mode is Mode.HALTED


def test_gap_through_loss_line_when_flat_halts_in_one_update() -> None:
    update = step(open_window(1, D(100), START), "85", flat=True)
    assert [t.current for t in update.transitions] == [Mode.DEFENSE, Mode.HALTED]


def test_halted_stays_halted_until_rollover() -> None:
    state = step(open_window(1, D(100), START), "91", flat=True).state
    assert step(state, "130", flat=True).state.mode is Mode.HALTED
    rolled = step(state, "92", flat=True, hours=24)
    assert rolled.state.mode is Mode.NORMAL
    assert rolled.state.window_id == 2
    assert rolled.state.e0 == D(92)
    assert rolled.closed is not None
    assert rolled.closed.halt_reason is HaltReason.LOSS_LINE


def test_defense_finishes_closing_before_rollover() -> None:
    state = step(open_window(1, D(100), START), "91").state
    assert step(state, "90", hours=25).state.mode is Mode.DEFENSE
    assert step(state, "90", flat=True, hours=25).state.mode is Mode.HALTED


equity_paths = st.lists(
    st.tuples(
        st.decimals(min_value=50, max_value=150, places=2),
        st.booleans(),
    ),
    min_size=1,
    max_size=60,
)


@given(equity_paths)
def test_mode_invariants_within_a_window(path) -> None:
    state = open_window(1, D(100), START)
    limits = CONFIG.window
    seen_flat_mode = seen_protect = False
    previous_floor = None
    for equity, flat in path:
        state = update_window(state, equity, START, CONFIG, flat=flat).state
        if seen_flat_mode:
            assert state.mode in FLAT_MODES
        if seen_protect:
            assert state.mode in (Mode.PROTECT, Mode.HALTED)
        if state.lock_floor is not None:
            assert state.lock_floor >= state.e0 * (1 + limits.lock_floor_min)
            if previous_floor is not None:
                assert state.lock_floor >= previous_floor
            previous_floor = state.lock_floor
        if state.mode in (Mode.NORMAL, Mode.CAUTION):
            assert state.loss_line(limits) < equity < state.target(limits)
        if state.mode in ENTRY_MODES - {Mode.PROTECT}:
            assert state.halt_reason is None
        seen_flat_mode |= state.mode in FLAT_MODES
        seen_protect |= state.mode is Mode.PROTECT
        assert state.trough <= equity <= state.peak
