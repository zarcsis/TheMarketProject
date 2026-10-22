from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from urllib.parse import parse_qs

import httpx
import pytest

from marketbot.domain import Side, tighten_to_tick
from marketbot.exchange.futures import (
    STOP_MARKET,
    BinanceFutures,
    first_bracket_maintenance_rate,
    other_wallets_total,
    parse_conditional,
    parse_position,
    parse_symbol_info,
)
from marketbot.exchange.rest import BinanceREST
from marketbot.execution.executor import ManagedPosition
from marketbot.risk.window import HaltReason, Mode, WindowState
from marketbot.state.store import Store, load_dataclass

pytestmark = pytest.mark.anyio
D = Decimal

SOL_INFO = {
    "symbol": "SOLUSDT",
    "contractType": "PERPETUAL",
    "filters": [
        {"filterType": "PRICE_FILTER", "tickSize": "0.0100"},
        {"filterType": "LOT_SIZE", "stepSize": "0.01", "minQty": "0.01", "maxQty": "1000000"},
        {"filterType": "MARKET_LOT_SIZE", "stepSize": "0.01", "minQty": "0.01", "maxQty": "5000"},
        {"filterType": "MIN_NOTIONAL", "notional": "5"},
    ],
}


class FakeSigner:
    def sign(self, payload: str) -> str:
        return "sig"


def test_parse_symbol_info() -> None:
    info = parse_symbol_info(SOL_INFO)
    assert info.tick_size == D("0.0100")
    assert info.market_max_qty == D(5000)
    assert info.min_notional == D(5)


def test_parse_position_sides() -> None:
    row = {"symbol": "SOLUSDT", "positionAmt": "-1.5", "entryPrice": "100", "markPrice": "99"}
    position = parse_position(row)
    assert position is not None
    assert position.side is Side.SHORT
    assert position.qty == D("1.5")
    assert parse_position({**row, "positionAmt": "0.000"}) is None


def test_parse_conditional_and_protection() -> None:
    order = parse_conditional(
        {
            "algoId": 2146760,
            "clientAlgoId": "mb-1-stop",
            "orderType": "STOP_MARKET",
            "symbol": "SOLUSDT",
            "side": "SELL",
            "triggerPrice": "98.00",
            "closePosition": True,
        }
    )
    position = parse_position(
        {"symbol": "SOLUSDT", "positionAmt": "1", "entryPrice": "100", "markPrice": "100"}
    )
    assert order.kind == STOP_MARKET
    assert order.protects(position)


def test_wallets_and_brackets() -> None:
    rows = [
        {"walletName": "Spot", "balance": "23.1"},
        {"walletName": "USDⓈ-M Futures", "balance": "100"},
        {"walletName": "Funding", "balance": "0.9"},
    ]
    assert other_wallets_total(rows) == D(24)
    brackets = [
        {
            "symbol": "SOLUSDT",
            "brackets": [
                {"notionalFloor": 5000, "maintMarginRatio": 0.01},
                {"notionalFloor": 0, "maintMarginRatio": 0.005},
            ],
        }
    ]
    assert first_bracket_maintenance_rate(brackets, "SOLUSDT") == D("0.005")


def test_stop_rounding_is_tighter() -> None:
    assert tighten_to_tick(D("97.991"), Side.LONG, D("0.01")) == D("98.00")
    assert tighten_to_tick(D("102.009"), Side.SHORT, D("0.01")) == D("102.00")


async def test_requests_use_the_algo_service() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/fapi/v1/algoOrder" and request.method == "POST":
            return httpx.Response(200, json={"algoId": 7, "clientAlgoId": "mb-x-stop"})
        if request.url.path == "/fapi/v1/order" and request.method == "POST":
            # Demo Trading's RESULT response has no avgPrice.
            return httpx.Response(
                200,
                json={"symbol": "SOLUSDT", "orderId": 1, "status": "FILLED", "executedQty": "1.36"},
            )
        if request.url.path == "/fapi/v1/order":
            return httpx.Response(
                200,
                json={
                    "symbol": "SOLUSDT",
                    "orderId": 1,
                    "status": "FILLED",
                    "executedQty": "1.36",
                    "avgPrice": "100.05",
                },
            )
        return httpx.Response(200, json={})

    rest = BinanceREST(
        "https://fapi.example",
        "/fapi/v1/time",
        api_key="key",
        signer=FakeSigner(),
        transport=httpx.MockTransport(handler),
    )
    venue = BinanceFutures(rest)
    stop = await venue.place_conditional("SOLUSDT", "SELL", STOP_MARKET, D("98.00"), "mb-x-stop")
    fill = await venue.market("SOLUSDT", "SELL", D("1.36"), reduce_only=True, client_id="mb-x")
    await venue.cancel_conditional("SOLUSDT", stop.algo_id)
    await rest.close()

    placed = parse_qs(requests[0].url.query.decode())
    assert placed["algoType"] == ["CONDITIONAL"]
    assert placed["type"] == ["STOP_MARKET"]
    assert placed["triggerPrice"] == ["98.00"]
    assert placed["closePosition"] == ["true"]
    assert placed["workingType"] == ["MARK_PRICE"]
    assert "quantity" not in placed
    market = parse_qs(requests[1].url.query.decode())
    assert market["reduceOnly"] == ["true"]
    assert market["newOrderRespType"] == ["RESULT"]
    assert fill.avg_price == D("100.05")
    assert (requests[2].method, requests[2].url.path) == ("GET", "/fapi/v1/order")
    cancel = requests[3]
    assert (cancel.method, cancel.url.path) == ("DELETE", "/fapi/v1/algoOrder")
    assert parse_qs(cancel.url.query.decode())["algoId"] == ["7"]


def test_store_round_trips_state(tmp_path: Path) -> None:
    store = Store(tmp_path / "bot.db")
    window = WindowState(
        3,
        datetime(2026, 10, 22, tzinfo=UTC),
        D("123.4"),
        D("130"),
        D("120"),
        Mode.PROTECT,
        lock_floor=D("134.5"),
        halt_reason=HaltReason.LOCK_FLOOR,
        trades=2,
    )
    store.put("window", window)
    assert load_dataclass(WindowState, store.get("window")) == window

    position = ManagedPosition("SOLUSDT", Side.LONG, D("1.36"), D("100.05"), D("98.00"), 7)
    store.put_position("SOLUSDT", position)
    assert load_dataclass(ManagedPosition, store.positions()["SOLUSDT"]) == position

    command_id = store.enqueue("propose", {"symbol": "SOLUSDT"})
    assert [c.id for c in store.pending()] == [command_id]
    store.complete(command_id, "done", {"ok": True})
    assert store.wait(command_id, timeout_s=1).result == {"ok": True}
    store.log("universe", day=datetime(2026, 10, 22).date(), symbols=["A"])
    store.log("decision", kind="not a clash")
    assert store.last_events(1)[0].payload == {"kind": "not a clash"}


async def test_reduce_only_stop_request() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"algoId": 9})

    rest = BinanceREST(
        "https://fapi.example",
        "/fapi/v1/time",
        api_key="key",
        signer=FakeSigner(),
        transport=httpx.MockTransport(handler),
    )
    stop = await BinanceFutures(rest).place_conditional(
        "SOLUSDT", "SELL", STOP_MARKET, D("98.00"), "mb-x-stop", qty=D("1.59")
    )
    await rest.close()
    placed = parse_qs(requests[0].url.query.decode())
    assert placed["quantity"] == ["1.59"]
    assert placed["reduceOnly"] == ["true"]
    assert "closePosition" not in placed
    assert stop.reduce_only and stop.qty == D("1.59") and not stop.close_position


async def test_symbol_info_reloads_for_new_listings() -> None:
    listed: list[dict] = [SOL_INFO]
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path == "/fapi/v1/exchangeInfo":
            return httpx.Response(200, json={"symbols": list(listed)})
        symbol = parse_qs(request.url.query.decode())["symbol"][0]
        return httpx.Response(
            200,
            json=[{"symbol": symbol, "brackets": [{"notionalFloor": 0, "maintMarginRatio": 0.01}]}],
        )

    rest = BinanceREST(
        "https://fapi.example",
        "/fapi/v1/time",
        api_key="key",
        signer=FakeSigner(),
        transport=httpx.MockTransport(handler),
    )
    venue = BinanceFutures(rest)
    assert (await venue.symbol_info("SOLUSDT")).symbol == "SOLUSDT"
    listed.append({**SOL_INFO, "symbol": "NEWUSDT"})
    assert (await venue.symbol_info("NEWUSDT")).symbol == "NEWUSDT"
    with pytest.raises(ValueError, match="not a perpetual"):
        await venue.symbol_info("NONEUSDT")
    await rest.close()
    assert calls.count("/fapi/v1/exchangeInfo") == 3
