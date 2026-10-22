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
