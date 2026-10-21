"""Pre-trade checks and position sizing under the open-risk budget (plan section 4.3, 4.4).

The invariant: after any approved entry, equity minus the loss of every stop firing at
once (with exit fees and slippage) stays at or above the entry floor.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal
from enum import StrEnum

from marketbot.risk.config import Costs, RiskConfig
from marketbot.risk.window import ENTRY_MODES, Mode, WindowState


class Side(StrEnum):
    LONG = "LONG"
    SHORT = "SHORT"


@dataclass(frozen=True)
class OpenPosition:
    symbol: str
    side: Side
    qty: Decimal
    mark: Decimal
    stop: Decimal | None
    bucket: str = "btc_beta"

    @property
    def notional(self) -> Decimal:
        return self.qty * self.mark


@dataclass(frozen=True)
class TradeIntent:
    symbol: str
    side: Side
    entry: Decimal
    stop: Decimal
    bucket: str = "btc_beta"
    strategy: str = "manual"


@dataclass(frozen=True)
class SymbolRules:
    step_size: Decimal
    min_qty: Decimal
    min_notional: Decimal
    maintenance_margin_rate: Decimal = Decimal("0.01")


@dataclass(frozen=True)
class Decision:
    approved: bool
    reasons: tuple[str, ...]
    qty: Decimal = Decimal(0)
    risk: Decimal = Decimal(0)
    leverage: int = 0

    @classmethod
    def reject(cls, *reasons: str) -> "Decision":
        return cls(False, reasons)


def position_risk(position: OpenPosition, costs: Costs) -> Decimal:
    """Equity lost from the current mark if the position's stop fires."""
    if position.stop is None:
        return position.notional  # unprotected: everything is at risk
    move = (
        position.mark - position.stop
        if position.side is Side.LONG
        else position.stop - position.mark
    )
    exit_cost = position.qty * position.stop * (costs.taker_fee + costs.stop_slippage)
    return max(move, Decimal(0)) * position.qty + exit_cost


def total_risk(positions: Sequence[OpenPosition], costs: Costs) -> Decimal:
    return sum((position_risk(position, costs) for position in positions), Decimal(0))


def entry_floor(state: WindowState, config: RiskConfig) -> Decimal | None:
    """The equity level that open risk must never be able to breach; None blocks entries."""
    limits = config.window
    if state.mode in (Mode.NORMAL, Mode.CAUTION):
        return state.loss_line(limits) + limits.loss_line_buffer * state.e0
    if state.mode is Mode.PROTECT:
        return state.lock_floor
    return None


def unit_risk(intent: TradeIntent, costs: Costs) -> Decimal:
    """Loss per unit from entry to stop, paying taker fees both ways plus stop slippage."""
    return (
        abs(intent.entry - intent.stop)
        + intent.entry * costs.taker_fee
        + intent.stop * (costs.taker_fee + costs.stop_slippage)
    )


def size_trade(
    intent: TradeIntent,
    state: WindowState,
    equity: Decimal,
    positions: Sequence[OpenPosition],
    rules: SymbolRules,
    free_margin: Decimal,
    config: RiskConfig,
    *,
    risk_scale: Decimal = Decimal(1),
    project_multiplier: Decimal = Decimal(1),
) -> Decision:
    """Size an entry, or reject it with the reasons."""
    trade, leverage, costs = config.trade, config.leverage, config.costs

    if state.mode not in ENTRY_MODES:
        return Decision.reject(f"mode {state.mode} allows no entries")
    floor = entry_floor(state, config)
    assert floor is not None
    if not Decimal(0) <= risk_scale <= 1:
        return Decision.reject("risk scale must be between 0 and 1")
    if state.trades >= trade.max_trades_per_window:
        return Decision.reject("trade limit for this window reached")
    max_positions = (
        trade.caution_max_positions if state.mode is Mode.CAUTION else trade.max_positions
    )
    if len(positions) >= max_positions:
        return Decision.reject(f"{len(positions)} positions open; limit is {max_positions}")
    if any(position.symbol == intent.symbol for position in positions):
        return Decision.reject("a position in this symbol is already open")
    same_direction = sum(
        position.bucket == intent.bucket and position.side is intent.side for position in positions
    )
    if same_direction >= trade.max_same_direction_per_bucket:
        return Decision.reject(f"too many {intent.side} positions in bucket {intent.bucket}")
    stop_on_wrong_side = (
        intent.stop >= intent.entry if intent.side is Side.LONG else intent.stop <= intent.entry
    )
    if intent.entry <= 0 or intent.stop <= 0 or stop_on_wrong_side:
        return Decision.reject("stop must be on the losing side of the entry")

    size = {Mode.CAUTION: trade.caution_size, Mode.PROTECT: trade.protect_size}.get(
        state.mode, Decimal(1)
    )
    budget = trade.risk_per_trade * state.e0 * size * risk_scale * project_multiplier
    available = equity - total_risk(positions, costs) - floor
    allowed = min(budget, available)
    if allowed <= 0:
        return Decision.reject("no risk budget left above the floor")

    reasons: list[str] = []
    qty = allowed / unit_risk(intent, costs)

    open_notional = sum((position.notional for position in positions), Decimal(0))
    notional_room = leverage.max_effective * equity - open_notional
    if qty * intent.entry > notional_room:
        qty = max(notional_room, Decimal(0)) / intent.entry
        reasons.append("reduced by the effective leverage cap")

    # Isolated margin: keep liquidation several stop distances away.
    stop_fraction = abs(intent.entry - intent.stop) / intent.entry
    safe = 1 / (
        leverage.liquidation_distance_multiple * stop_fraction + rules.maintenance_margin_rate
    )
    max_setting = min(int(safe.to_integral_value(ROUND_FLOOR)), leverage.max_exchange)
    if max_setting < 1:
        return Decision.reject("stop too wide to keep liquidation away")
    if free_margin <= 0:
        return Decision.reject("no free margin")
    if qty * intent.entry > free_margin * max_setting:
        qty = free_margin * max_setting / intent.entry
        reasons.append("reduced by free margin")

    qty = (qty / rules.step_size).to_integral_value(ROUND_FLOOR) * rules.step_size
    notional = qty * intent.entry
    if qty < rules.min_qty or notional < rules.min_notional:
        return Decision.reject(
            f"size {qty} ({notional:.2f} notional) is below the symbol minimum",
            *reasons,
        )
    setting = max(1, int((notional / free_margin).to_integral_value(ROUND_CEILING)))
    risk = qty * unit_risk(intent, costs)
    assert equity - total_risk(positions, costs) - risk >= floor
    return Decision(True, tuple(reasons), qty, risk, min(setting, max_setting))


@dataclass(frozen=True)
class StopChange:
    symbol: str
    stop: Decimal | None  # None: close the position


def tighten_stops(
    positions: Sequence[OpenPosition], equity: Decimal, floor: Decimal, costs: Costs
) -> list[StopChange]:
    """Move stops so that every stop firing at once keeps equity at or above `floor`.

    Risk shrinks by the same factor for every position. Execution rounds the new stops
    to the tick size towards the mark (tighter).
    """
    current = [position_risk(position, costs) for position in positions]
    allowed = equity - floor
    if sum(current, Decimal(0)) <= allowed:
        return []
    if allowed <= 0:
        return [StopChange(position.symbol, None) for position in positions]
    factor = allowed / sum(current, Decimal(0))
    fee = costs.taker_fee + costs.stop_slippage
    changes = []
    for position, risk in zip(positions, current, strict=True):
        if position.stop is None:
            changes.append(StopChange(position.symbol, None))
            continue
        target = risk * factor
        mark_value = position.mark * position.qty
        # risk = |mark - stop| * qty + stop * qty * fee, solved for stop.
        if position.side is Side.LONG:
            stop = (mark_value - target) / (position.qty * (1 - fee))
            crossed = stop >= position.mark
        else:
            stop = (mark_value + target) / (position.qty * (1 + fee))
            crossed = stop <= position.mark
        # Even exiting at the mark costs more than the allowed risk: close now.
        changes.append(StopChange(position.symbol, None if crossed else stop))
    return changes
