from marketbot.data.announcements import airdropped_perpetuals, delisted_perpetuals


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


def test_parse_airdrop_titles() -> None:
    hodler = (
        "Introducing Midnight (NIGHT) on Binance HODLer Airdrops! Earn NIGHT With Retroactive "
        "BNB Simple Earn Subscriptions"
    )
    assert airdropped_perpetuals(hodler) == ["NIGHTUSDT"]
    launchpool = "Introducing Scroll (SCR) on Binance Launchpool and Pre-Market!"
    assert airdropped_perpetuals(launchpool) == ["SCRUSDT"]
    megadrop = (
        "Introducing Solv Protocol (SOLV) on Binance Megadrop! Lock Your BNB and Complete Web3 "
        "Quests for Airdrop"
    )
    assert airdropped_perpetuals(megadrop) == ["SOLVUSDT"]
    assert airdropped_perpetuals("Introducing Binance Alpha: Discover Emerging Crypto") == []
    assert airdropped_perpetuals("Binance Will List Hyperliquid (HYPE) with Seed Tag Applied") == []


def test_parse_upbit_titles() -> None:
    from marketbot.data.upbit import events, tickers

    assert events("웜홀(W) 신규 거래지원 안내 (KRW, BTC, USDT 마켓)") == [("W", "krw listing")]
    assert events("페페(PEPE) KRW 마켓 디지털 자산 추가") == [("PEPE", "krw listing")]
    assert events("봉크(BONK) 신규 거래지원 안내 (USDT 마켓)") == [("BONK", "other listing")]
    assert events(
        "셀레스티아(TIA)(KRW, BTC, USDT 마켓), 아이오넷(IO)(BTC, USDT 마켓) 신규 거래지원 안내"
    ) == [("TIA", "krw listing"), ("IO", "other listing")]
    assert tickers("PUFFER(퍼퍼), SWELL(스웰네트워크) 신규 거래지원 안내 (BTC, USDT 마켓)") == [
        "PUFFER",
        "SWELL",
    ]
    assert events("질리카(ZIL) 거래지원 종료 안내 (11/16 15:00)") == [("ZIL", "delisting")]
    assert events("다드(DAD), 비트코인골드(BTG) 거래 유의 종목 지정 안내") == [
        ("DAD", "warning"),
        ("BTG", "warning"),
    ]
    assert events("다드(DAD) 거래 유의 종목 지정 기간 연장 안내") == []
    assert events("신세틱스(SNX) 거래 유의 종목 지정 해제 안내") == []
    assert events("스톰엑스(STMX) 유의 촉구 안내") == [("STMX", "caution call")]
    assert events("USDT 마켓 디지털 자산 추가 (AGLD, AHT, ARPA)") == []


def test_upbit_tokens_map_to_binance_perpetuals() -> None:
    from marketbot.research.upbit import perpetual

    symbols = {"WUSDT", "1000PEPEUSDT", "1000000BOBUSDT"}
    assert perpetual("W", symbols) == "WUSDT"
    assert perpetual("PEPE", symbols) == "1000PEPEUSDT"
    assert perpetual("BOB", symbols) == "1000000BOBUSDT"
    assert perpetual("ZIL", symbols) is None


def test_parse_bybit_and_okx_titles() -> None:
    from marketbot.data.exchanges import events, perp_delistings

    assert events("bybit", "Delisting of  CHESSUSDT Perpetual Contract") == [
        ("CHESS", "perp delisting")
    ]
    assert events(
        "bybit",
        "Delisting of XEC, ORT and MV",
        "Bybit will be delisting the XEC/USDT, ORT/USDT and MV/USDT trading pairs",
    ) == [("XEC", "spot delisting"), ("ORT", "spot delisting"), ("MV", "spot delisting")]
    assert events("bybit", "Delisting of Selected Perpetual Contracts (Nov 24, 2025)") == []
    assert events("bybit", "Bybit to Delist 1 Token(s) on Oct 28, 2026") == []
    assert events("bybit", "New Listing: FUEL/USDT — Grab a Share of the FUEL Prize Pool") == [
        ("FUEL", "spot listing")
    ]
    assert events("bybit", "New Listing:  RONINUSDT Perpetual Contract, with up to 50x") == [
        ("RONIN", "perp listing")
    ]
    assert events("okx", "OKX to delist perpetual futures for BADGER, BAL, NIL crypto") == [
        ("BADGER", "perp delisting"),
        ("BAL", "perp delisting"),
        ("NIL", "perp delisting"),
    ]
    assert events("okx", "OKX to delist KISHU, MILO and MXC spot trading pairs") == [
        ("KISHU", "spot delisting"),
        ("MILO", "spot delisting"),
        ("MXC", "spot delisting"),
    ]
    assert events("okx", "OKX to list PARTI (Particle Network) for spot trading") == [
        ("PARTI", "spot listing")
    ]
    assert events(
        "okx", "OKX to list perpetual for MOVE crypto, along with its margin trading"
    ) == [("MOVE", "perp listing")]
    assert events("okx", "OKX to delist several perpetual futures") == []
    assert events("okx", "OKX to list perpetual futures for USAR, COST and LLY equities") == []
    assert perp_delistings("Delisting of DATAUSDT Perpetual  Contract") == ["DATAUSDT"]
    assert perp_delistings("OKX to delist perpetual futures for AI16Z crypto") == ["AI16ZUSDT"]
    assert perp_delistings("Delisting of PELL, PURSE and TRC") == []
