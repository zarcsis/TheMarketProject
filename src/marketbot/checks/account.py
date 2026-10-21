"""Phase 0 checks: connectivity, API key safety, and account setup.

Every request here is read-only; nothing changes account state.
"""

import json
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

import httpx

from marketbot.exchange.client import Binance
from marketbot.exchange.rest import BinanceAPIError, BinanceREST

REFERENCE_SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT", "DOGEUSDT")
MAX_CLOCK_OFFSET_MS = 1000


class Status(StrEnum):
    OK = "OK"
    INFO = "INFO"
    WARN = "WARN"
    FAIL = "FAIL"


@dataclass(frozen=True)
class CheckResult:
    name: str
    status: Status
    detail: str


async def run_checks(binance: Binance, *, include_private: bool) -> list[CheckResult]:
    results = await _check_connectivity(binance)
    if any(result.status is Status.FAIL for result in results):
        return results
    results += await _check_symbol_filters(binance)
    if include_private:
        results += await _check_private(binance)
    return results


# Evaluation of API responses. Pure functions, unit-tested without network access.


def evaluate_clock(host: str, offset_ms: int) -> CheckResult:
    status = Status.OK if abs(offset_ms) <= MAX_CLOCK_OFFSET_MS else Status.WARN
    return CheckResult(f"Clock offset ({host})", status, f"{offset_ms:+d} ms vs. server time")


def evaluate_api_restrictions(data: dict[str, Any]) -> list[CheckResult]:
    def flag(field: str) -> bool | None:
        value = data.get(field)
        return value if isinstance(value, bool) else None

    results = [
        CheckResult("Key: withdrawals", Status.OK, "disabled")
        if flag("enableWithdrawals") is False
        else CheckResult(
            "Key: withdrawals", Status.FAIL, "enabled or not reported; disable withdrawals"
        ),
        CheckResult("Key: IP restriction", Status.OK, "restricted to trusted IPs")
        if flag("ipRestrict") is True
        else CheckResult(
            "Key: IP restriction", Status.FAIL, "not restricted; enable IP access restriction"
        ),
        CheckResult("Key: reading", Status.OK, "enabled")
        if flag("enableReading") is True
        else CheckResult("Key: reading", Status.FAIL, "disabled; the bot cannot see the account"),
    ]
    required = (
        ("enableSpotAndMarginTrading", "Key: spot and margin trading", "needed for spot orders"),
        ("enableFutures", "Key: futures", "needed for USD-M futures orders"),
        ("permitsUniversalTransfer", "Key: universal transfer", "needed to move funds"),
    )
    for field, name, reason in required:
        results.append(
            CheckResult(name, Status.OK, "enabled")
            if flag(field)
            else CheckResult(name, Status.WARN, f"disabled; {reason}")
        )
    results.append(
        CheckResult(
            "Key: margin loan and repay",
            Status.INFO,
            f"{_on_off(flag('enableMargin'))} (optional; only for margin shorts)",
        )
    )
    if flag("enableInternalTransfer"):
        results.append(
            CheckResult("Key: internal transfer", Status.WARN, "enabled; not needed, disable it")
        )
    expiration = data.get("tradingAuthorityExpirationTime")
    if isinstance(expiration, int) and expiration > 0:
        results.append(
            CheckResult("Key: trading authority expires", Status.INFO, _format_ms_date(expiration))
        )
    return results


def evaluate_spot_account(data: dict[str, Any]) -> list[CheckResult]:
    results = [
        CheckResult("Spot account: trading", Status.OK, "allowed")
        if data.get("canTrade")
        else CheckResult("Spot account: trading", Status.WARN, "not allowed")
    ]
    rates = data.get("commissionRates") or {}
    if "maker" in rates and "taker" in rates:
        results.append(
            CheckResult(
                "Fees: spot",
                Status.INFO,
                f"maker {_percent(rates['maker'])}, taker {_percent(rates['taker'])}",
            )
        )
    balances = [
        (row["asset"], Decimal(row["free"]) + Decimal(row["locked"]))
        for row in data.get("balances", [])
    ]
    results.append(
        CheckResult("Spot balances", Status.INFO, _join_amounts(b for b in balances if b[1] > 0))
    )
    return results


def evaluate_wallets(rows: list[dict[str, Any]], quote: str) -> list[CheckResult]:
    wallets = [(row["walletName"], Decimal(row["balance"])) for row in rows]
    funded = [(name, balance) for name, balance in wallets if balance > 0]
    total = sum((balance for _, balance in wallets), Decimal(0))
    return [
        CheckResult(f"Wallets (in {quote})", Status.INFO, _join_amounts(funded)),
        CheckResult("Total equity estimate", Status.INFO, f"{_decimal(total)} {quote}"),
    ]


def evaluate_futures(
    account: dict[str, Any],
    position_mode: dict[str, Any],
    multi_assets: dict[str, Any],
    commission: dict[str, Any],
) -> list[CheckResult]:
    open_positions = [
        row["symbol"]
        for row in account.get("positions", [])
        if Decimal(str(row.get("positionAmt", "0"))) != 0
    ]
    return [
        CheckResult(
            "Futures balances",
            Status.INFO,
            f"wallet {_decimal(account['totalWalletBalance'])} USDT, "
            f"unrealized PnL {_decimal(account['totalUnrealizedProfit'])} USDT, "
            f"available {_decimal(account['availableBalance'])} USDT",
        ),
        CheckResult(
            "Futures open positions",
            Status.INFO,
            ", ".join(open_positions) if open_positions else "none",
        ),
        CheckResult(
            "Futures position mode", Status.WARN, "hedge mode; the bot expects one-way mode"
        )
        if position_mode.get("dualSidePosition")
        else CheckResult("Futures position mode", Status.OK, "one-way"),
        CheckResult(
            "Futures asset mode",
            Status.WARN,
            "multi-assets mode; the bot expects single-asset mode (needed for isolated margin)",
        )
        if multi_assets.get("multiAssetsMargin")
        else CheckResult("Futures asset mode", Status.OK, "single-asset"),
        CheckResult(
            "Fees: USD-M futures",
            Status.INFO,
            f"maker {_percent(commission['makerCommissionRate'])}, "
            f"taker {_percent(commission['takerCommissionRate'])}",
        ),
    ]


def spot_min_notional(symbol_info: dict[str, Any]) -> Decimal | None:
    for item in symbol_info.get("filters", []):
        if item.get("filterType") in ("NOTIONAL", "MIN_NOTIONAL"):
            return Decimal(item["minNotional"])
    return None


def futures_min_notional(symbol_info: dict[str, Any]) -> Decimal | None:
    for item in symbol_info.get("filters", []):
        if item.get("filterType") == "MIN_NOTIONAL":
            return Decimal(item["notional"])
    return None


# Network side.


async def _check_connectivity(binance: Binance) -> list[CheckResult]:
    results: list[CheckResult] = []
    for name, host in (("spot", binance.spot), ("futures", binance.futures)):
        try:
            offset = await host.sync_time()
        except (BinanceAPIError, httpx.HTTPError) as exc:
            results.append(
                CheckResult(f"Connectivity ({name})", Status.FAIL, f"{type(exc).__name__}: {exc}")
            )
            continue
        results.append(CheckResult(f"Connectivity ({name})", Status.OK, "reachable"))
        results.append(evaluate_clock(name, offset))
    return results


async def _check_symbol_filters(binance: Binance) -> list[CheckResult]:
    try:
        spot_info = await binance.spot.public(
            "GET",
            "/api/v3/exchangeInfo",
            {"symbols": json.dumps(list(REFERENCE_SYMBOLS), separators=(",", ":"))},
        )
        futures_info = await binance.futures.public("GET", "/fapi/v1/exchangeInfo")
    except (BinanceAPIError, httpx.HTTPError) as exc:
        return [CheckResult("Exchange info", Status.WARN, f"{type(exc).__name__}: {exc}")]

    spot_symbols = {row["symbol"]: row for row in spot_info["symbols"]}
    futures_symbols = {row["symbol"]: row for row in futures_info["symbols"]}
    perpetuals = [
        row
        for row in futures_info["symbols"]
        if row.get("contractType") == "PERPETUAL"
        and row.get("quoteAsset") == "USDT"
        and row.get("status") == "TRADING"
    ]
    results = [CheckResult("USDT perpetuals trading", Status.INFO, str(len(perpetuals)))]
    for symbol in REFERENCE_SYMBOLS:
        spot = spot_min_notional(spot_symbols[symbol]) if symbol in spot_symbols else None
        futures = (
            futures_min_notional(futures_symbols[symbol]) if symbol in futures_symbols else None
        )
        results.append(
            CheckResult(
                f"Min notional {symbol}",
                Status.INFO,
                f"spot {_decimal(spot)} USDT, futures {_decimal(futures)} USDT",
            )
        )
    return results


async def _check_private(binance: Binance) -> list[CheckResult]:
    spot = binance.spot
    try:
        restrictions = await spot.signed("GET", "/sapi/v1/account/apiRestrictions")
    except (BinanceAPIError, httpx.HTTPError) as exc:
        return [CheckResult("Key permissions", Status.FAIL, f"cannot verify: {exc}")]
    results = evaluate_api_restrictions(restrictions)

    results += await _optional(
        "Spot account",
        evaluate_spot_account,
        spot,
        "/api/v3/account",
        {"omitZeroBalances": True},
    )
    results += await _check_wallets(spot)
    results += await _optional(
        "BNB fee discount (spot)",
        lambda data: [
            CheckResult("BNB fee discount (spot)", Status.INFO, _on_off(data.get("spotBNBBurn")))
        ],
        spot,
        "/sapi/v1/bnbBurn",
    )
    results += await _optional(
        "Simple Earn flexible",
        lambda data: [
            CheckResult(
                "Simple Earn flexible",
                Status.INFO,
                _join_amounts(
                    (row["asset"], Decimal(row["totalAmount"])) for row in data.get("rows", [])
                ),
            )
        ],
        spot,
        "/sapi/v1/simple-earn/flexible/position",
    )
    results += await _check_futures(binance.futures)
    return results


async def _check_wallets(spot: BinanceREST) -> list[CheckResult]:
    try:
        rows = await spot.signed("GET", "/sapi/v1/asset/wallet/balance", {"quoteAsset": "USDT"})
        return evaluate_wallets(rows, "USDT")
    except BinanceAPIError:
        pass
    try:
        rows = await spot.signed("GET", "/sapi/v1/asset/wallet/balance")
    except (BinanceAPIError, httpx.HTTPError) as exc:
        return [CheckResult("Wallets", Status.WARN, f"unavailable: {exc}")]
    return evaluate_wallets(rows, "BTC")


async def _check_futures(futures: BinanceREST) -> list[CheckResult]:
    try:
        account = await futures.signed("GET", "/fapi/v3/account")
        position_mode = await futures.signed("GET", "/fapi/v1/positionSide/dual")
        multi_assets = await futures.signed("GET", "/fapi/v1/multiAssetsMargin")
        commission = await futures.signed("GET", "/fapi/v1/commissionRate", {"symbol": "BTCUSDT"})
    except (BinanceAPIError, httpx.HTTPError) as exc:
        return [CheckResult("Futures account", Status.WARN, f"unavailable: {exc}")]
    results = evaluate_futures(account, position_mode, multi_assets, commission)
    results += await _optional(
        "BNB fee discount (futures)",
        lambda data: [
            CheckResult("BNB fee discount (futures)", Status.INFO, _on_off(data.get("feeBurn")))
        ],
        futures,
        "/fapi/v1/feeBurn",
    )
    return results


async def _optional(
    name: str,
    evaluate: Callable[[Any], list[CheckResult]],
    host: BinanceREST,
    path: str,
    params: Mapping[str, Any] | None = None,
) -> list[CheckResult]:
    try:
        data = await host.signed("GET", path, params)
    except (BinanceAPIError, httpx.HTTPError) as exc:
        return [CheckResult(name, Status.WARN, f"unavailable: {exc}")]
    return evaluate(data)


# Formatting.


def render_text(results: list[CheckResult]) -> str:
    width = max(len(result.name) for result in results)
    lines = [f"[{r.status.value:<4}] {r.name:<{width}}  {r.detail}" for r in results]
    return "\n".join([*lines, "", _summary(results)])


def render_markdown(results: list[CheckResult], generated_at: datetime) -> str:
    lines = [
        "# Account check",
        "",
        f"Generated: {generated_at.strftime('%Y-%m-%d %H:%M UTC')}",
        "",
        "| Status | Check | Detail |",
        "|---|---|---|",
    ]
    lines += [f"| {r.status.value} | {r.name} | {r.detail.replace('|', '/')} |" for r in results]
    return "\n".join([*lines, "", _summary(results), ""])


def _summary(results: list[CheckResult]) -> str:
    counts = {status: sum(r.status is status for r in results) for status in Status}
    return ", ".join(f"{status.value}: {count}" for status, count in counts.items())


def _join_amounts(items: Iterable[tuple[str, Decimal]]) -> str:
    parts = [f"{name} {_decimal(amount)}" for name, amount in items]
    return ", ".join(parts) if parts else "none"


def _decimal(value: Decimal | str | None) -> str:
    if value is None:
        return "n/a"
    return format(Decimal(value).normalize(), "f")


def _percent(rate: str) -> str:
    return f"{float(rate) * 100:.4g}%"


def _on_off(value: bool | None) -> str:
    if value is None:
        return "unknown"
    return "on" if value else "off"


def _format_ms_date(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=UTC).strftime("%Y-%m-%d")
