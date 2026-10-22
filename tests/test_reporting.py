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
