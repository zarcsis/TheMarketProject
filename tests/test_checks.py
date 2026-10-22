from decimal import Decimal

import pytest

from marketbot.checks.account import (
    Status,
    evaluate_api_restrictions,
    evaluate_clock,
    evaluate_futures,
    evaluate_wallets,
    futures_min_notional,
    spot_min_notional,
)

SAFE_RESTRICTIONS = {
    "ipRestrict": True,
    "enableReading": True,
    "enableWithdrawals": False,
    "enableInternalTransfer": False,
    "enableMargin": True,
    "enableFutures": True,
    "permitsUniversalTransfer": True,
    "enableSpotAndMarginTrading": True,
}


def statuses(results) -> dict[str, Status]:
    return {result.name: result.status for result in results}


def test_safe_key_has_no_failures_or_warnings() -> None:
    results = evaluate_api_restrictions(SAFE_RESTRICTIONS)
    assert all(result.status in (Status.OK, Status.INFO) for result in results)


@pytest.mark.parametrize(
    ("field", "value", "check"),
    [
        ("enableWithdrawals", True, "Key: withdrawals"),
        ("enableWithdrawals", None, "Key: withdrawals"),
        ("ipRestrict", False, "Key: IP restriction"),
        ("enableReading", False, "Key: reading"),
    ],
)
def test_unsafe_key_settings_fail(field, value, check) -> None:
    data = {**SAFE_RESTRICTIONS, field: value}
    if value is None:
        del data[field]
    assert statuses(evaluate_api_restrictions(data))[check] is Status.FAIL


def test_missing_trading_permissions_warn() -> None:
    data = {**SAFE_RESTRICTIONS, "enableFutures": False}
    assert statuses(evaluate_api_restrictions(data))["Key: futures"] is Status.WARN


def test_internal_transfer_warns() -> None:
    data = {**SAFE_RESTRICTIONS, "enableInternalTransfer": True}
    assert statuses(evaluate_api_restrictions(data))["Key: internal transfer"] is Status.WARN


def test_clock_offset_threshold() -> None:
    assert evaluate_clock("spot", -999).status is Status.OK
    assert evaluate_clock("spot", 1500).status is Status.WARN


def test_wallet_total() -> None:
    rows = [
        {"walletName": "Spot", "balance": "60.5"},
        {"walletName": "USDⓈ-M Futures", "balance": "39.5"},
        {"walletName": "Funding", "balance": "0"},
    ]
    results = evaluate_wallets(rows, "USDT")
    assert results[0].detail == "Spot 60.5, USDⓈ-M Futures 39.5"
    assert results[1].detail == "100 USDT"


def test_futures_modes_warn_when_not_one_way_single_asset() -> None:
    account = {
        "totalWalletBalance": "40.0",
        "totalUnrealizedProfit": "0.0",
        "availableBalance": "40.0",
        "positions": [{"symbol": "ETHUSDT", "positionAmt": "0.01"}],
    }
    commission = {"makerCommissionRate": "0.0002", "takerCommissionRate": "0.0005"}
    results = statuses(
        evaluate_futures(
            account, {"dualSidePosition": True}, {"multiAssetsMargin": True}, commission
        )
    )
    assert results["Futures position mode"] is Status.WARN
    assert results["Futures asset mode"] is Status.WARN


def test_min_notional_extraction() -> None:
    spot = {"filters": [{"filterType": "LOT_SIZE"}, {"filterType": "NOTIONAL", "minNotional": "5"}]}
    legacy_spot = {"filters": [{"filterType": "MIN_NOTIONAL", "minNotional": "10.0"}]}
    futures = {"filters": [{"filterType": "MIN_NOTIONAL", "notional": "100"}]}
    assert spot_min_notional(spot) == Decimal(5)
    assert spot_min_notional(legacy_spot) == Decimal(10)
    assert futures_min_notional(futures) == Decimal(100)
    assert futures_min_notional({"filters": []}) is None


def test_unrestricted_ip_warns_only_when_the_owner_allows_it() -> None:
    data = {**SAFE_RESTRICTIONS, "ipRestrict": False, "enableMargin": False}
    assert statuses(evaluate_api_restrictions(data))["Key: IP restriction"] is Status.FAIL
    allowed = statuses(evaluate_api_restrictions(data, allow_unrestricted_ip=True))
    assert allowed["Key: IP restriction"] is Status.WARN
    assert allowed["Key: withdrawals"] is Status.OK


def test_margin_loans_warn_on_an_unrestricted_key() -> None:
    data = {**SAFE_RESTRICTIONS, "ipRestrict": False, "enableMargin": True}
    results = statuses(evaluate_api_restrictions(data, allow_unrestricted_ip=True))
    assert results["Key: margin loan and repay"] is Status.WARN


def test_withdrawals_still_fail_with_an_unrestricted_key() -> None:
    data = {**SAFE_RESTRICTIONS, "ipRestrict": False, "enableWithdrawals": True}
    results = statuses(evaluate_api_restrictions(data, allow_unrestricted_ip=True))
    assert results["Key: withdrawals"] is Status.FAIL
