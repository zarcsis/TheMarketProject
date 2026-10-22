from marketbot.market import trend_line


def test_trend_line() -> None:
    rows = [[0, 0, 0, 0, str(100 + i)] for i in range(60)]
    line = trend_line("BTC", rows)
    assert line.startswith("BTC: 159.00 | 1d +0.6%, 7d +4.6%, 30d +23.3%")
    assert "vs MA20 +6.4%" in line
