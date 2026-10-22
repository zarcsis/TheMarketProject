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


def test_parse_monitoring_tag_titles() -> None:
    from marketbot.data.announcements import monitoring_tagged_perpetuals

    title = (
        "Binance Will Extend the Monitoring Tag to Include FARM, HIGH, MLN & VELODROME, "
        "Remove the Seed Tag for XAUT on 2026-04-14"
    )
    # The removal clause is dropped; the four tagged tokens remain.
    assert monitoring_tagged_perpetuals(title) == [
        "FARMUSDT",
        "HIGHUSDT",
        "MLNUSDT",
        "VELODROMEUSDT",
    ]
    assert monitoring_tagged_perpetuals(
        "Binance Will Extend the Monitoring Tag to Include BICO & CVC on 2026-10-09"
    ) == ["BICOUSDT", "CVCUSDT"]
