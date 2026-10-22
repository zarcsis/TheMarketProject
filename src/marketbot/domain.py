"""Small types shared by the exchange, risk, and execution layers."""

from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal
from enum import StrEnum


class Side(StrEnum):
    LONG = "LONG"
    SHORT = "SHORT"

    @property
    def entry_order_side(self) -> str:
        return "BUY" if self is Side.LONG else "SELL"

    @property
    def exit_order_side(self) -> str:
        return "SELL" if self is Side.LONG else "BUY"


def floor_to_step(value: Decimal, step: Decimal) -> Decimal:
    return (value / step).to_integral_value(ROUND_FLOOR) * step


def ceil_to_step(value: Decimal, step: Decimal) -> Decimal:
    return (value / step).to_integral_value(ROUND_CEILING) * step


def tighten_to_tick(stop: Decimal, side: Side, tick: Decimal) -> Decimal:
    """Round a stop price to the tick grid towards the position (the tighter side)."""
    return ceil_to_step(stop, tick) if side is Side.LONG else floor_to_step(stop, tick)
