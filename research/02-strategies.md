# Research 02: Intraday technical strategies

Status: complete, 2026-10-22.

Full tables:

- [results/02-strategies.md](results/02-strategies.md) for 2025-10 to 2026-09;
- [results/02-strategies-2024-25.md](results/02-strategies-2024-25.md) for 2024-10 to 2025-09.

Code: [`backtest/`](../src/marketbot/backtest/) and [`research/strategies.py`](../src/marketbot/research/strategies.py).

## Question

Research 01 showed that the daily window game loses without an edge. Can an intraday signal with asymmetric trades (risk 1R, aim for 2R or more, or trail the stop) earn a positive expectancy after costs?

## Method

**Signal families**, computed on 1-hour bars:

- **Donchian breakout:** a close beyond the prior 12 or 24 hours with a volume surge; the stop is 1.5 or 3 ATR away.
- **Fade:** trades against a bar that moved more than 3 or 4 ATR on 3x or 4x volume; the stop goes beyond the bar's extreme.
- **Opening-range breakout:** the first 5-minute close beyond the 00:00 UTC range of 2 or 4 hours; the stop is at the range's midpoint or far side.

**Exits:**

- a 1R, 2R, or 3R target;
- or a 3 ATR chandelier trailing stop with a move to break-even at 1R;
- every position closes by the end of the UTC day.

**Variants and universes.**

- **Universes:** the research 01 universes (majors, the top 20 by volume, and the 10 largest movers), each chosen from the previous day's data. Only crypto contracts are included; Binance's TradFi perpetuals are excluded.
- **BTC trend filter:** each variant is run with and without it.
- **Count:** 208 variants with trades.

**Simulation on 5-minute bars:**

- the entry fills at the next bar's open;
- a bar that touches both the stop and the target counts as a stop;
- a stop that the bar opens beyond fills at the open;
- costs are a 0.05% taker fee per side, 0.02% entry slippage, and 0.1% stop slippage.

**Two independent years**, each split into halves:

| Year | In-sample | Out-of-sample |
|---|---|---|
| 2024-10 to 2025-09 | 2024-10 to 2025-03 | 2025-04 to 2025-09 |
| 2025-10 to 2026-09 | 2025-10 to 2026-03 | 2026-04 to 2026-09 |

## Findings

### 1. No family has an edge after costs

Mean R per trade by family (all variants pooled):

| Family | 2024-25 H1 | 2024-25 H2 | 2025-26 H1 | 2025-26 H2 |
|---|---|---|---|---|
| Donchian breakout | −0.05 | −0.03 | +0.01 | −0.04 |
| Fade | −0.46 | −0.22 | −0.32 | −0.12 |
| Opening range | −0.07 | −0.10 | −0.10 | −0.14 |

Costs are about 0.07R per trade at the typical 3% stop distance, so the breakout family is roughly break-even before costs and negative after them.

### 2. In-sample winners do not survive

The best in-sample variants mostly turn negative in the next half-year, the classic sign of selecting noise among 208 variants.

One variant was positive in three of four half-years: a 24-hour Donchian breakout with a 3x volume surge, a 1.5 ATR stop, and a trailing exit, traded on the 10 largest movers. It returned −0.09, +0.20, +0.06, and +0.08 R, averaging about +0.07R. Its t-statistics are 0.6–1.7 per half-year, which is not evidence. It runs on Demo Trading only, to exercise the live strategy pipeline.

## Implications

- **Simple intraday technical rules do not beat costs** on Binance USD-M perpetuals in either year. More parameter search on these families would mostly fit noise.
- **Look for structural edges instead.** These are effects with a reason to persist, such as forced selling and supply events. Research 03 and 04 find two.

## Limitations

- **Bar resolution.** With 5-minute bars, stop-first tie-breaking understates targets for very tight stops.
- **Funding** is not included. Intraday holds pay at most three funding periods.
- **Survivorship.** The universes include delisted symbols, but only months in which a symbol was in a universe were downloaded.
