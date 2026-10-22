from marketbot.data.announcements import delisted_perpetuals


def test_parse_delisting_titles() -> None:
    assert delisted_perpetuals("Binance Will Delist ACX, HFT, PIVX on 2026-08-17") == [
        "ACXUSDT",
        "HFTUSDT",
        "PIVXUSDT",
    ]
    assert delisted_perpetuals(
        "Binance Futures Will Delist PTBUSDT and MITOUSDT USDⓈ-M Perpetual Contracts (2026-10-23)"
    ) == ["MITOUSDT", "PTBUSDT"]
    assert delisted_perpetuals("Binance Will Delist EPIC and QUICK on 2026-10-28") == [
        "EPICUSDT",
        "QUICKUSDT",
    ]
    assert delisted_perpetuals("Notice of Removal of Spot Trading Pairs - 2026-10-24") == []
