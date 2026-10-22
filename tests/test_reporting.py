from marketbot.reporting import summarize_income


def test_summarize_income_by_symbol() -> None:
    rows = [
        {"symbol": "DOGEUSDT", "incomeType": "REALIZED_PNL", "income": "1.5"},
        {"symbol": "DOGEUSDT", "incomeType": "COMMISSION", "income": "-0.2"},
        {"symbol": "SUIUSDT", "incomeType": "FUNDING_FEE", "income": "-0.05"},
        {"symbol": "", "incomeType": "TRANSFER", "income": "100"},
    ]
    summary = {row["symbol"]: row for row in summarize_income(rows).iter_rows(named=True)}
    assert summary["DOGEUSDT"]["net"] == 1.3
    assert summary["SUIUSDT"]["funding"] == -0.05
    assert "-" not in summary  # transfers are not trading income


def test_render_is_phone_friendly() -> None:
    from datetime import UTC, datetime

    from marketbot.reporting import render

    rows = [
        {"symbol": "DOGEUSDT", "incomeType": "REALIZED_PNL", "income": "4.73"},
        {"symbol": "DOGEUSDT", "incomeType": "COMMISSION", "income": "-0.2269"},
        {"symbol": "1000PEPEUSDT", "incomeType": "REALIZED_PNL", "income": "-0.1417"},
    ]
    journal = {
        "entries": [],
        "entries_by_strategy": {},
        "closes": {},
        "mode_changes": [],
        "errors": {},
    }
    from collections import Counter

    journal["entries_by_strategy"] = Counter()
    journal["closes"] = Counter()
    text = render(
        datetime(2026, 10, 22, tzinfo=UTC),
        datetime(2026, 10, 22, 12, tzinfo=UTC),
        summarize_income(rows),
        journal,
    )
    assert "Net +4.36 USDT" in text
    assert "|" not in text
    assert max(len(line) for line in text.splitlines()) <= 40
    assert "DOGE          +4.50   +4.73  -0.23" in text


def test_summary_block() -> None:
    from collections import Counter

    from marketbot.reporting import summary_block

    snapshot = {
        "window": {"mode": "PROTECT", "e0": "125"},
        "account": {"equity": "136.89", "positions": [{"symbol": "DOGEUSDT", "side": "SHORT"}]},
    }
    journal = {
        "entries": [{}, {}],
        "closes": Counter({"stop": 1}),
        "mode_changes": [{"previous": "NORMAL", "current": "PROTECT"}],
        "errors": {},
    }
    income = summarize_income(
        [{"symbol": "DOGEUSDT", "incomeType": "REALIZED_PNL", "income": "4.5"}]
    )
    lines = summary_block("demo", True, snapshot, income, journal)
    assert lines[0] == "DEMO: bot running, mode PROTECT"
    assert "equity 136.89 (window start 125.00, +9.5%)" in lines[1]
    assert "DOGEUSDT SHORT" in lines[2]
    assert "2 entries, 1 closes, net +4.50 USDT" in lines[3]
