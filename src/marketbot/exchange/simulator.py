"""In-memory USD-M futures venue for tests and offline drills.

Prices move only when a test calls `set_mark`. Conditional orders trigger on the mark
price and fill at the new mark, so a jump through a stop fills at the worse price, like a
gap. `fail_next` makes the next call of an operation raise, to rehearse failures.
"""

from dataclasses import dataclass, replace
from decimal import Decimal
from itertools import count

import httpx

from marketbot.domain import Side
from marketbot.exchange.futures import (
    STOP_MARKET,
    TAKE_PROFIT_MARKET,
    AccountState,
    ConditionalOrder,
    Fill,
    Position,
    SymbolInfo,
)
from marketbot.exchange.rest import BinanceAPIError

WOULD_IMMEDIATELY_TRIGGER = -2021
REDUCE_ONLY_REJECTED = -2022
CLOSE_POSITION_EXISTS = -4130


@dataclass
class _Holding:
    side: Side
    qty: Decimal
    entry: Decimal


class SimulatedExchange:
    def __init__(
        self,
        symbols: dict[str, SymbolInfo],
        marks: dict[str, Decimal],
        *,
        balance: Decimal = Decimal(100),
        other_wallets: Decimal = Decimal(0),
        taker_fee: Decimal = Decimal("0.0005"),
    ) -> None:
        self.symbols = symbols
        self.marks = dict(marks)
        self.wallet = balance
        self.other_wallets = other_wallets
        self.taker_fee = taker_fee
        self.holdings: dict[str, _Holding] = {}
        self.conditional: dict[int, ConditionalOrder] = {}
        self.leverage: dict[str, int] = {}
        self.calls: list[str] = []
        self._failures: dict[str, Exception] = {}
        self._ids = count(1)

    # Test controls.

    def fail_next(self, operation: str, error: Exception | None = None) -> None:
        self._failures[operation] = error or httpx.ConnectError("simulated network failure")

    def set_mark(self, symbol: str, price: Decimal) -> None:
        self.marks[symbol] = price
        for order in list(self.conditional.values()):
            if order.algo_id not in self.conditional:
                continue  # removed by an earlier trigger in this loop
            if order.symbol == symbol and _triggered(order, price):
                del self.conditional[order.algo_id]
                holding = self.holdings.get(symbol)
                if holding and holding.side.exit_order_side == order.order_side:
                    qty = holding.qty if order.close_position else min(order.qty, holding.qty)
                    self._close(symbol, qty, price)

    def open_foreign_position(self, symbol: str, side: Side, qty: Decimal) -> None:
        """A position the bot did not open (for the reconciliation drill)."""
        self._trade(symbol, side.entry_order_side, qty, self.marks[symbol])

    # Exchange protocol.

    async def account(self) -> AccountState:
        self._call("account")
        positions = {
            symbol: Position(
                symbol=symbol,
                side=holding.side,
                qty=holding.qty,
                entry_price=holding.entry,
                mark_price=self.marks[symbol],
                liquidation_price=Decimal(0),
            )
            for symbol, holding in self.holdings.items()
        }
        unrealized = sum((self._pnl(symbol) for symbol in self.holdings), Decimal(0))
        margin = sum(
            (
                holding.qty * holding.entry / self.leverage.get(symbol, 1)
                for symbol, holding in self.holdings.items()
            ),
            Decimal(0),
        )
        return AccountState(
            margin_balance=self.wallet + unrealized,
            available_balance=self.wallet + unrealized - margin,
            other_wallets=self.other_wallets,
            positions=positions,
            conditional_orders=tuple(self.conditional.values()),
        )

    async def symbol_info(self, symbol: str) -> SymbolInfo:
        self._call("symbol_info")
        return self.symbols[symbol]

    async def mark_price(self, symbol: str) -> Decimal:
        self._call("mark_price")
        return self.marks[symbol]

    async def prepare(self, symbol: str, leverage: int) -> None:
        self._call("prepare")
        self.leverage[symbol] = leverage

    async def market(
        self, symbol: str, order_side: str, qty: Decimal, *, reduce_only: bool, client_id: str
    ) -> Fill:
        self._call("market")
        holding = self.holdings.get(symbol)
        if reduce_only and (
            holding is None or holding.side.exit_order_side != order_side or qty > holding.qty
        ):
            raise BinanceAPIError(400, REDUCE_ONLY_REJECTED, "ReduceOnly Order is rejected.")
        price = self.marks[symbol]
        self._trade(symbol, order_side, qty, price)
        self._call("market_after_fill")  # lets a test break the response of a filled order
        return Fill(symbol, next(self._ids), qty, price)

    async def place_conditional(
        self,
        symbol: str,
        order_side: str,
        kind: str,
        trigger: Decimal,
        client_id: str,
        *,
        qty: Decimal | None = None,
    ) -> ConditionalOrder:
        self._call("place_conditional")
        order = ConditionalOrder(
            symbol,
            next(self._ids),
            client_id,
            order_side,
            kind,
            trigger,
            close_position=qty is None,
            reduce_only=qty is not None,
            qty=qty,
        )
        if order.close_position and any(
            other.close_position
            and (other.symbol, other.order_side, other.kind) == (symbol, order_side, kind)
            for other in self.conditional.values()
        ):
            # Binance allows one close-position order per kind and direction.
            raise BinanceAPIError(400, CLOSE_POSITION_EXISTS, "An open stop or take profit ...")
        if _triggered(order, self.marks[symbol]):
            raise BinanceAPIError(
                400, WOULD_IMMEDIATELY_TRIGGER, "Order would immediately trigger."
            )
        self.conditional[order.algo_id] = order
        return order

    async def cancel_conditional(self, symbol: str, algo_id: int) -> None:
        self._call("cancel_conditional")
        self.conditional.pop(algo_id, None)

    async def cancel_all(self, symbol: str) -> None:
        self._call("cancel_all")
        for algo_id in [o.algo_id for o in self.conditional.values() if o.symbol == symbol]:
            del self.conditional[algo_id]

    # Internals.

    def _call(self, operation: str) -> None:
        self.calls.append(operation)
        error = self._failures.pop(operation, None)
        if error is not None:
            raise error

    def _pnl(self, symbol: str) -> Decimal:
        holding = self.holdings[symbol]
        direction = 1 if holding.side is Side.LONG else -1
        return direction * (self.marks[symbol] - holding.entry) * holding.qty

    def _trade(self, symbol: str, order_side: str, qty: Decimal, price: Decimal) -> None:
        self.wallet -= qty * price * self.taker_fee
        holding = self.holdings.get(symbol)
        side = Side.LONG if order_side == "BUY" else Side.SHORT
        if holding is None:
            self.holdings[symbol] = _Holding(side, qty, price)
        elif holding.side is side:
            total = holding.qty + qty
            entry = (holding.entry * holding.qty + price * qty) / total
            self.holdings[symbol] = replace(holding, qty=total, entry=entry)
        else:
            self._close(symbol, min(qty, holding.qty), price, fee_paid=True)
            if qty > holding.qty:
                self.holdings[symbol] = _Holding(side, qty - holding.qty, price)

    def _close(self, symbol: str, qty: Decimal, price: Decimal, *, fee_paid: bool = False) -> None:
        holding = self.holdings[symbol]
        direction = 1 if holding.side is Side.LONG else -1
        self.wallet += direction * (price - holding.entry) * qty
        if not fee_paid:
            self.wallet -= qty * price * self.taker_fee
        if qty >= holding.qty:
            del self.holdings[symbol]
            # Close-position orders expire with the position; reduce-only ones stay behind
            # as orphans, which the bot's reconciler must cancel.
            for order in [o for o in self.conditional.values() if o.symbol == symbol]:
                if order.close_position:
                    del self.conditional[order.algo_id]
        else:
            self.holdings[symbol] = replace(holding, qty=holding.qty - qty)


def _triggered(order: ConditionalOrder, mark: Decimal) -> bool:
    rises = (order.kind == STOP_MARKET) == (order.order_side == "BUY")
    assert order.kind in (STOP_MARKET, TAKE_PROFIT_MARKET)
    return mark >= order.trigger_price if rises else mark <= order.trigger_price
