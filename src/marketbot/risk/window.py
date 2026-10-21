"""The 24-hour trading window and its risk modes (plan section 4.1 and 4.2)."""

from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from decimal import Decimal
from enum import StrEnum

from marketbot.risk.config import RiskConfig, WindowLimits


class Mode(StrEnum):
    NORMAL = "NORMAL"
    CAUTION = "CAUTION"
    PROTECT = "PROTECT"
    DEFENSE = "DEFENSE"
    HALTED = "HALTED"


class HaltReason(StrEnum):
    LOSS_LINE = "loss line"
    LOCK_FLOOR = "lock floor"


ENTRY_MODES = frozenset({Mode.NORMAL, Mode.CAUTION, Mode.PROTECT})
# In these modes the account must hold no positions.
FLAT_MODES = frozenset({Mode.DEFENSE, Mode.HALTED})


@dataclass(frozen=True)
class WindowState:
    window_id: int
    opened_at: datetime
    e0: Decimal
    peak: Decimal
    trough: Decimal
    mode: Mode = Mode.NORMAL
    lock_floor: Decimal | None = None
    halt_reason: HaltReason | None = None
    trades: int = 0

    def target(self, limits: WindowLimits) -> Decimal:
        return self.e0 * (1 + limits.target)

    def loss_line(self, limits: WindowLimits) -> Decimal:
        return self.e0 * (1 - limits.loss_line)

    def closes_at(self, limits: WindowLimits) -> datetime:
        return self.opened_at + timedelta(hours=limits.length_hours)


@dataclass(frozen=True)
class Transition:
    previous: Mode
    current: Mode
    reason: str


@dataclass(frozen=True)
class WindowUpdate:
    state: WindowState
    transitions: tuple[Transition, ...] = ()
    # The window that ended, when this update rolled over to a new one.
    closed: WindowState | None = None


def open_window(window_id: int, equity: Decimal, now: datetime) -> WindowState:
    if equity <= 0:
        raise ValueError("Cannot open a window with non-positive equity")
    return WindowState(window_id, now, e0=equity, peak=equity, trough=equity)


def lock_floor(e0: Decimal, peak: Decimal, limits: WindowLimits) -> Decimal:
    return max(e0 * (1 + limits.lock_floor_min), e0 + limits.lock_floor_share * (peak - e0))


def update_window(
    state: WindowState, equity: Decimal, now: datetime, config: RiskConfig, *, flat: bool
) -> WindowUpdate:
    """Apply a new equity mark. `flat` says whether the account holds no positions."""
    limits = config.window
    # A window in DEFENSE first finishes closing positions, then rolls over.
    if now >= state.closes_at(limits) and state.mode is not Mode.DEFENSE:
        new = open_window(state.window_id + 1, equity, now)
        return WindowUpdate(new, (Transition(state.mode, new.mode, "window rollover"),), state)

    current = replace(state, peak=max(state.peak, equity), trough=min(state.trough, equity))
    transitions: list[Transition] = []

    def move(mode: Mode, reason: str, **changes: object) -> None:
        nonlocal current
        transitions.append(Transition(current.mode, mode, reason))
        current = replace(current, mode=mode, **changes)  # type: ignore[arg-type]

    if current.mode is Mode.PROTECT:
        assert current.lock_floor is not None
        floor = max(current.lock_floor, lock_floor(current.e0, current.peak, limits))
        current = replace(current, lock_floor=floor)
        if equity <= floor:
            move(Mode.HALTED, "equity at lock floor", halt_reason=HaltReason.LOCK_FLOOR)
    elif current.mode in (Mode.NORMAL, Mode.CAUTION):
        if equity >= current.target(limits):
            move(
                Mode.PROTECT,
                "target reached",
                lock_floor=lock_floor(current.e0, current.peak, limits),
            )
        elif equity <= current.loss_line(limits):
            move(Mode.DEFENSE, "loss line breached", halt_reason=HaltReason.LOSS_LINE)
        elif current.mode is Mode.NORMAL and equity <= current.e0 * (1 - limits.caution_at):
            move(Mode.CAUTION, "drawdown reached caution level")
        elif current.mode is Mode.CAUTION and equity >= current.e0 * (
            1 - limits.caution_recover_at
        ):
            move(Mode.NORMAL, "recovered from caution level")

    if current.mode is Mode.DEFENSE and flat:
        move(Mode.HALTED, "positions closed after loss line")
    return WindowUpdate(current, tuple(transitions))


def record_trade(state: WindowState) -> WindowState:
    return replace(state, trades=state.trades + 1)
