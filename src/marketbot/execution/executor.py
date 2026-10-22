"""Order execution: entries with a mandatory exchange-side stop, stop moves, close-all.

Rules (plan section 4.4):
- A stop is placed right after the entry fills. If it cannot be placed, the position is
  closed at once.
- A stop moves by placing the new one first and cancelling the old one second, so the
  position is never unprotected.
- Close-all closes positions first and cancels orders second, so a failed close leaves
  the stops in place.
"""

import itertools
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import httpx

from marketbot.domain import Side, floor_to_step, tighten_to_tick
from marketbot.exchange.futures import (
    STOP_MARKET,
    TAKE_PROFIT_MARKET,
    ConditionalOrder,
    Exchange,
    SymbolInfo,
)
from marketbot.exchange.rest import BinanceAPIError
from marketbot.risk.sizing import Decision, TradeIntent

EXCHANGE_ERRORS = (BinanceAPIError, httpx.HTTPError, RuntimeError)

Journal = Callable[..., Any]


@dataclass(frozen=True)
class ManagedPosition:
    symbol: str
    side: Side
    qty: Decimal
    entry_price: Decimal
    stop: Decimal
    stop_algo_id: int | None
    target: Decimal | None = None
    target_algo_id: int | None = None
    strategy: str = "manual"
    bucket: str = "btc_beta"
    opened_at: datetime | None = None


class EntryFailed(Exception):
    pass


class Executor:
    def __init__(self, exchange: Exchange, journal: Journal) -> None:
        self.exchange = exchange
        self._journal = journal
        self._sequence = itertools.count(1)
        self._run = format(int(time.time()) % 10**8, "x")

    def client_id(self, purpose: str) -> str:
        # Binance allows [.A-Z:/a-z0-9_-]{1,36}; unique across restarts thanks to _run.
        return f"mb-{self._run}-{next(self._sequence)}-{purpose}"[:36]

    async def enter(
        self,
        intent: TradeIntent,
        decision: Decision,
        info: SymbolInfo,
        *,
        target: Decimal | None = None,
    ) -> ManagedPosition:
        qty = floor_to_step(decision.qty, info.market_step_size)
        if qty <= 0 or qty * intent.entry < info.min_notional:
            raise EntryFailed(f"quantity {qty} is below the symbol minimum")
        await self.exchange.prepare(intent.symbol, decision.leverage)
        try:
            fill = await self.exchange.market(
                intent.symbol,
                intent.side.entry_order_side,
                qty,
                reduce_only=False,
                client_id=self.client_id("entry"),
            )
        except Exception as exc:
            # The order may have filled although the call failed (a timeout, an unexpected
            # response). An entry without a confirmed fill gets no stop, so close any fill now.
            self._journal("entry_uncertain", symbol=intent.symbol, error=repr(exc))
            await self._close_if_open(intent.symbol)
            raise EntryFailed(f"entry failed ({exc!r}); any fill was closed") from exc
        self._journal(
            "entry_filled",
            symbol=intent.symbol,
            side=intent.side,
            qty=fill.qty,
            price=fill.avg_price,
            strategy=intent.strategy,
        )
        stop_price = tighten_to_tick(intent.stop, intent.side, info.tick_size)
        try:
            stop = await self._place_with_retry(
                intent.symbol, intent.side, STOP_MARKET, stop_price, qty=fill.qty
            )
        except Exception as exc:
            self._journal("stop_failed", symbol=intent.symbol, error=str(exc))
            await self.close(
                intent.symbol, intent.side, fill.qty, reason="stop could not be placed"
            )
            raise EntryFailed(f"stop could not be placed ({exc}); position closed") from exc

        target_order = None
        if target is not None:
            target_price = tighten_to_tick(target, _opposite(intent.side), info.tick_size)
            try:
                target_order = await self._place_with_retry(
                    intent.symbol, intent.side, TAKE_PROFIT_MARKET, target_price
                )
            except EXCHANGE_ERRORS as exc:
                # The stop protects the position; a missing target only costs upside.
                self._journal("target_failed", symbol=intent.symbol, error=str(exc))

        return ManagedPosition(
            symbol=intent.symbol,
            side=intent.side,
            qty=fill.qty,
            entry_price=fill.avg_price,
            stop=stop.trigger_price,
            stop_algo_id=stop.algo_id,
            target=target_order.trigger_price if target_order else None,
            target_algo_id=target_order.algo_id if target_order else None,
            strategy=intent.strategy,
            bucket=intent.bucket,
            opened_at=datetime.now(UTC),
        )

    async def place_stop(self, managed: ManagedPosition, info: SymbolInfo) -> ManagedPosition:
        """(Re)place the stop of a position whose stop is missing on the exchange."""
        price = tighten_to_tick(managed.stop, managed.side, info.tick_size)
        stop = await self._place_with_retry(
            managed.symbol, managed.side, STOP_MARKET, price, qty=managed.qty
        )
        return replace(managed, stop=stop.trigger_price, stop_algo_id=stop.algo_id)

    async def move_stop(
        self, managed: ManagedPosition, new_stop: Decimal, info: SymbolInfo
    ) -> ManagedPosition:
        moved = await self.place_stop(replace(managed, stop=new_stop), info)
        if managed.stop_algo_id is not None:
            try:
                await self.exchange.cancel_conditional(managed.symbol, managed.stop_algo_id)
            except EXCHANGE_ERRORS as exc:
                # Two stops are harmless; the reconciler cancels the extra one.
                self._journal("cancel_failed", symbol=managed.symbol, error=str(exc))
        self._journal("stop_moved", symbol=managed.symbol, old=managed.stop, new=moved.stop)
        return moved

    async def close(self, symbol: str, side: Side, qty: Decimal, *, reason: str) -> None:
        await self.exchange.market(
            symbol, side.exit_order_side, qty, reduce_only=True, client_id=self.client_id("exit")
        )
        self._journal("position_closed", symbol=symbol, qty=qty, reason=reason)

    async def flatten(self, reason: str, attempts: int = 3) -> bool:
        """Close every position and cancel every order; True when the account is flat."""
        for _ in range(attempts):
            try:
                account = await self.exchange.account()
            except EXCHANGE_ERRORS as exc:
                self._journal("flatten_error", stage="account", error=str(exc))
                continue
            symbols = (
                set(account.positions)
                | {order.symbol for order in account.conditional_orders}
                | set(account.open_order_symbols)
            )
            if not symbols:
                return True
            for position in account.positions.values():
                try:
                    await self.close(position.symbol, position.side, position.qty, reason=reason)
                except EXCHANGE_ERRORS as exc:
                    self._journal("flatten_error", symbol=position.symbol, error=str(exc))
            for symbol in symbols:
                try:
                    await self.exchange.cancel_all(symbol)
                except EXCHANGE_ERRORS as exc:
                    self._journal("flatten_error", symbol=symbol, error=str(exc))
        try:
            account = await self.exchange.account()
        except EXCHANGE_ERRORS:
            return False
        return not account.positions and not account.conditional_orders

    async def _close_if_open(self, symbol: str) -> None:
        try:
            account = await self.exchange.account()
            position = account.positions.get(symbol)
            if position is not None:
                await self.close(symbol, position.side, position.qty, reason="unconfirmed entry")
        except Exception as exc:
            # The reconciler closes unknown positions on the next tick.
            self._journal("close_failed", symbol=symbol, error=repr(exc))

    async def _place_with_retry(
        self, symbol: str, side: Side, kind: str, trigger: Decimal, *, qty: Decimal | None = None
    ) -> ConditionalOrder:
        """Stops are reduce-only for `qty` so that a new one can go in before the old one is
        cancelled; targets close the whole position."""
        try:
            return await self._place(symbol, side, kind, trigger, qty)
        except BinanceAPIError:
            raise  # the exchange rejected it; retrying the same request will not help
        except EXCHANGE_ERRORS:
            return await self._place(symbol, side, kind, trigger, qty)

    async def _place(
        self, symbol: str, side: Side, kind: str, trigger: Decimal, qty: Decimal | None
    ) -> ConditionalOrder:
        purpose = "stop" if kind == STOP_MARKET else "target"
        return await self.exchange.place_conditional(
            symbol, side.exit_order_side, kind, trigger, self.client_id(purpose), qty=qty
        )


def _opposite(side: Side) -> Side:
    return Side.SHORT if side is Side.LONG else Side.LONG
