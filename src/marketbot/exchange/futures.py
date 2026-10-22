"""USD-M futures trading: account state, symbol rules, market orders, conditional orders.

Since 2025-12-09 conditional orders (STOP_MARKET, TAKE_PROFIT_MARKET, ...) live in the
Algo Service: placed, listed, and cancelled through `/fapi/v1/algoOrder` endpoints. The
regular order endpoints reject them with error -4120.
"""

import asyncio
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Protocol

from marketbot.domain import Side
from marketbot.exchange.rest import BinanceAPIError, BinanceREST

STOP_MARKET = "STOP_MARKET"
TAKE_PROFIT_MARKET = "TAKE_PROFIT_MARKET"
NO_NEED_TO_CHANGE_MARGIN_TYPE = -4046
FUTURES_WALLET = "USDⓈ-M Futures"


@dataclass(frozen=True)
class SymbolInfo:
    symbol: str
    tick_size: Decimal
    step_size: Decimal
    market_step_size: Decimal
    min_qty: Decimal
    market_max_qty: Decimal
    min_notional: Decimal
    maintenance_margin_rate: Decimal = Decimal("0.01")


@dataclass(frozen=True)
class Position:
    symbol: str
    side: Side
    qty: Decimal  # absolute
    entry_price: Decimal
    mark_price: Decimal
    liquidation_price: Decimal

    @property
    def notional(self) -> Decimal:
        return self.qty * self.mark_price


@dataclass(frozen=True)
class ConditionalOrder:
    symbol: str
    algo_id: int
    client_id: str
    order_side: str  # BUY or SELL
    kind: str  # STOP_MARKET or TAKE_PROFIT_MARKET
    trigger_price: Decimal
    close_position: bool
    reduce_only: bool = False
    qty: Decimal | None = None  # for reduce-only orders

    def protects(self, position: Position) -> bool:
        """Whether this is a stop that closes the given position."""
        return (
            self.kind == STOP_MARKET
            and self.symbol == position.symbol
            and self.order_side == position.side.exit_order_side
            and (self.close_position or self.reduce_only)
        )


@dataclass(frozen=True)
class Fill:
    symbol: str
    order_id: int
    qty: Decimal
    avg_price: Decimal


@dataclass(frozen=True)
class AccountState:
    margin_balance: Decimal  # futures wallet plus unrealized PnL
    available_balance: Decimal
    other_wallets: Decimal  # every other wallet in USDT (spot, funding, earn)
    positions: dict[str, Position] = field(default_factory=dict)
    conditional_orders: tuple[ConditionalOrder, ...] = ()
    open_order_symbols: frozenset[str] = frozenset()

    @property
    def equity(self) -> Decimal:
        return self.margin_balance + self.other_wallets

    def stops_for(self, position: Position) -> list[ConditionalOrder]:
        return [order for order in self.conditional_orders if order.protects(position)]


class Exchange(Protocol):
    """What execution needs from a futures venue; the simulator implements it too."""

    async def account(self) -> AccountState: ...

    async def symbol_info(self, symbol: str) -> SymbolInfo: ...

    async def mark_price(self, symbol: str) -> Decimal: ...

    async def prepare(self, symbol: str, leverage: int) -> None: ...

    async def market(
        self, symbol: str, order_side: str, qty: Decimal, *, reduce_only: bool, client_id: str
    ) -> Fill: ...

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
        """A reduce-only order for `qty`, or a close-position order when `qty` is None."""
        ...

    async def cancel_conditional(self, symbol: str, algo_id: int) -> None: ...

    async def cancel_all(self, symbol: str) -> None: ...


# Parsing. Pure functions, unit-tested without network access.


def parse_symbol_info(row: dict[str, Any]) -> SymbolInfo:
    filters = {item["filterType"]: item for item in row["filters"]}
    lot = filters["LOT_SIZE"]
    market_lot = filters.get("MARKET_LOT_SIZE", lot)
    return SymbolInfo(
        symbol=row["symbol"],
        tick_size=Decimal(filters["PRICE_FILTER"]["tickSize"]),
        step_size=Decimal(lot["stepSize"]),
        market_step_size=Decimal(market_lot["stepSize"]),
        min_qty=Decimal(lot["minQty"]),
        market_max_qty=Decimal(market_lot["maxQty"]),
        min_notional=Decimal(filters["MIN_NOTIONAL"]["notional"]),
    )


def parse_position(row: dict[str, Any]) -> Position | None:
    amount = Decimal(row["positionAmt"])
    if amount == 0:
        return None
    return Position(
        symbol=row["symbol"],
        side=Side.LONG if amount > 0 else Side.SHORT,
        qty=abs(amount),
        entry_price=Decimal(row["entryPrice"]),
        mark_price=Decimal(row["markPrice"]),
        liquidation_price=Decimal(row.get("liquidationPrice") or "0"),
    )


def parse_conditional(row: dict[str, Any]) -> ConditionalOrder:
    return ConditionalOrder(
        symbol=row["symbol"],
        algo_id=int(row["algoId"]),
        client_id=str(row.get("clientAlgoId", "")),
        order_side=row["side"],
        kind=row.get("orderType") or row.get("type", ""),
        trigger_price=Decimal(str(row["triggerPrice"])),
        close_position=bool(row.get("closePosition")),
        reduce_only=bool(row.get("reduceOnly")),
        qty=Decimal(str(row["quantity"])) if row.get("quantity") else None,
    )


def parse_fill(row: dict[str, Any]) -> Fill:
    return Fill(
        symbol=row["symbol"],
        order_id=int(row["orderId"]),
        qty=Decimal(row["executedQty"]),
        avg_price=Decimal(row["avgPrice"]),
    )


def other_wallets_total(rows: list[dict[str, Any]]) -> Decimal:
    """Sum of every wallet except USD-M futures, which is measured with unrealized PnL."""
    return sum(
        (Decimal(row["balance"]) for row in rows if row["walletName"] != FUTURES_WALLET),
        Decimal(0),
    )


def first_bracket_maintenance_rate(rows: list[dict[str, Any]], symbol: str) -> Decimal:
    for row in rows:
        if row["symbol"] == symbol:
            brackets = sorted(row["brackets"], key=lambda bracket: bracket["notionalFloor"])
            return Decimal(str(brackets[0]["maintMarginRatio"]))
    raise KeyError(symbol)


# Network side.


class BinanceFutures:
    """The live (or demo) venue. `spot` is used only to value non-futures wallets."""

    def __init__(
        self,
        futures: BinanceREST,
        spot: BinanceREST | None = None,
        *,
        wallets_ttl_s: float = 60.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._futures = futures
        self._spot = spot
        self._wallets_ttl_s = wallets_ttl_s
        self._clock = clock
        self._symbols: dict[str, SymbolInfo] = {}
        self._other_wallets: tuple[float, Decimal] | None = None

    async def account(self) -> AccountState:
        account, positions, conditional, open_orders = await asyncio.gather(
            self._futures.signed("GET", "/fapi/v3/account"),
            self._futures.signed("GET", "/fapi/v3/positionRisk"),
            self._futures.signed("GET", "/fapi/v1/openAlgoOrders"),
            self._futures.signed("GET", "/fapi/v1/openOrders"),
        )
        parsed = [position for row in positions if (position := parse_position(row))]
        return AccountState(
            margin_balance=Decimal(account["totalMarginBalance"]),
            available_balance=Decimal(account["availableBalance"]),
            other_wallets=await self._other_wallets_value(),
            positions={position.symbol: position for position in parsed},
            conditional_orders=tuple(parse_conditional(row) for row in _rows(conditional)),
            open_order_symbols=frozenset(row["symbol"] for row in open_orders),
        )

    async def symbol_info(self, symbol: str) -> SymbolInfo:
        if symbol not in self._symbols:
            # (Re)load: a symbol listed while the bot runs is not in an older snapshot.
            info = await self._futures.public("GET", "/fapi/v1/exchangeInfo")
            self._symbols = {
                row["symbol"]: parse_symbol_info(row)
                for row in info["symbols"]
                if row.get("contractType") == "PERPETUAL"
            }
        if symbol not in self._symbols:
            raise ValueError(f"{symbol} is not a perpetual on this venue")
        base = self._symbols[symbol]
        brackets = await self._futures.signed("GET", "/fapi/v1/leverageBracket", {"symbol": symbol})
        rows = brackets if isinstance(brackets, list) else [brackets]
        rate = first_bracket_maintenance_rate(rows, symbol)
        return SymbolInfo(**{**base.__dict__, "maintenance_margin_rate": rate})

    async def mark_price(self, symbol: str) -> Decimal:
        data = await self._futures.public("GET", "/fapi/v1/premiumIndex", {"symbol": symbol})
        return Decimal(data["markPrice"])

    async def prepare(self, symbol: str, leverage: int) -> None:
        try:
            await self._futures.signed(
                "POST", "/fapi/v1/marginType", {"symbol": symbol, "marginType": "ISOLATED"}
            )
        except BinanceAPIError as exc:
            if exc.code != NO_NEED_TO_CHANGE_MARGIN_TYPE:
                raise
        await self._futures.signed(
            "POST", "/fapi/v1/leverage", {"symbol": symbol, "leverage": leverage}
        )

    async def market(
        self, symbol: str, order_side: str, qty: Decimal, *, reduce_only: bool, client_id: str
    ) -> Fill:
        row = await self._futures.signed(
            "POST",
            "/fapi/v1/order",
            {
                "symbol": symbol,
                "side": order_side,
                "type": "MARKET",
                "quantity": qty,
                "reduceOnly": True if reduce_only else None,
                "newClientOrderId": client_id,
                "newOrderRespType": "RESULT",
            },
        )
        for _ in range(5):
            # Some venues (Demo Trading) answer without avgPrice; the order query has it.
            if row.get("status") == "FILLED" and Decimal(row.get("avgPrice") or 0) > 0:
                return parse_fill(row)
            await asyncio.sleep(0.2)
            row = await self._futures.signed(
                "GET", "/fapi/v1/order", {"symbol": symbol, "orderId": row["orderId"]}
            )
        raise RuntimeError(f"Market order {client_id} not filled: status {row.get('status')}")

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
        # Binance allows one close-position stop per direction (error -4130), so stops that
        # must be replaced safely (new one first) are reduce-only orders with a quantity.
        size = {"quantity": qty, "reduceOnly": True} if qty else {"closePosition": True}
        row = await self._futures.signed(
            "POST",
            "/fapi/v1/algoOrder",
            {
                "algoType": "CONDITIONAL",
                "symbol": symbol,
                "side": order_side,
                "type": kind,
                "triggerPrice": trigger,
                **size,
                "workingType": "MARK_PRICE",
                "clientAlgoId": client_id,
            },
        )
        return ConditionalOrder(
            symbol=symbol,
            algo_id=int(row["algoId"]),
            client_id=str(row.get("clientAlgoId", client_id)),
            order_side=order_side,
            kind=kind,
            trigger_price=trigger,
            close_position=qty is None,
            reduce_only=qty is not None,
            qty=qty,
        )

    async def cancel_conditional(self, symbol: str, algo_id: int) -> None:
        await self._futures.signed("DELETE", "/fapi/v1/algoOrder", {"algoId": algo_id})

    async def cancel_all(self, symbol: str) -> None:
        await self._futures.signed("DELETE", "/fapi/v1/allOpenOrders", {"symbol": symbol})
        await self._futures.signed("DELETE", "/fapi/v1/algoOpenOrders", {"symbol": symbol})

    async def _other_wallets_value(self) -> Decimal:
        if self._spot is None:
            return Decimal(0)
        now = self._clock()
        if self._other_wallets is None or now - self._other_wallets[0] > self._wallets_ttl_s:
            rows = await self._spot.signed(
                "GET", "/sapi/v1/asset/wallet/balance", {"quoteAsset": "USDT"}
            )
            self._other_wallets = (now, other_wallets_total(rows))
        return self._other_wallets[1]


def _rows(data: Any) -> list[dict[str, Any]]:
    """Algo order listings come either as a list or wrapped in an object."""
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in ("orders", "rows", "data"):
            if isinstance(data.get(key), list):
                return data[key]
    return []
