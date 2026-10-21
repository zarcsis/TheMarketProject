# Research 01: Is +10% per window reachable?

Status: complete, 2026-10-21. Full tables: [results/01-feasibility.md](results/01-feasibility.md). Code: [`src/marketbot/research/feasibility.py`](../src/marketbot/research/feasibility.py).

## Question

The project's daily objective is +10% of equity per 24-hour window. A window stops at −9%. Before building strategies, this study measures three things:

1. Is the market volatile enough for +10% at a given leverage?
2. What happens without an edge, that is, when the trade direction is no better than chance?
3. How do those per-window outcomes compound?

## Method

- **Data.** One year of Binance USD-M perpetuals, 2025-10-01 to 2026-09-30, from the public archive: daily candles for all 871 USDT symbols, including delisted ones, and 5-minute candles for every symbol-day in the study.
- **Universes.** Each day's universe is chosen from the previous day's data only, so there is no look-ahead. There are three:
  - majors: BTC and ETH;
  - top 20 by volume: the 20 symbols with the largest previous-day quote volume;
  - in play: the 10 largest absolute previous-day moves among symbols with at least 50M USDT of volume.
- **The window game.** At 00:00 UTC, one position takes the window's whole equity at leverage L of 2, 3, 5, or 10.
  - The position exits at +10% of equity, at −9%, or at the end of the day.
  - Costs:
    - a taker fee of 0.05% on each side;
    - stop slippage of 0.1% of price;
    - a stop that gaps fills at the bar's open.
  - A 5-minute bar that touches both the target and the stop counts as a stop.
- **Direction rules.** Four simple rules, none of them a real strategy:
  - a coin flip;
  - always long;
  - the previous day's direction (momentum);
  - against the previous day's direction (reversal).

## Findings

### 1. Volatility is not the constraint

With perfect hindsight about direction, the target is usually within reach:

| Universe | Median best move from the open | Share of days reaching +10% at 3x | At 5x |
|---|---|---|---|
| Majors | 2.7% | 35% | 67% |
| Top 20 by volume | 5.8% | 71% | 87% |
| In play | 18.3% | 99% | 100% |

The market moves enough. The open question is whether the direction can be known in advance, and whether the target is reached before the stop.

### 2. Without an edge, every variant loses

All 48 combinations of universe, rule, and leverage have negative mean log growth per window. Log growth is the measure that decides long-run compounding.

The coin-flip baseline:

| Universe | Leverage | Target | Stop | Mean return per window | Log growth per window |
|---|---|---|---|---|---|
| Majors | 3x | 17% | 24% | −0.37% | −0.59% |
| Majors | 5x | 31% | 42% | −0.69% | −1.03% |
| Top 20 | 3x | 33% | 42% | −0.48% | −0.82% |
| Top 20 | 5x | 37% | 52% | −0.91% | −1.32% |
| In play | 3x | 44% | 56% | −0.64% | −1.08% |
| In play | 5x | 41% | 59% | −1.21% | −1.65% |

Windows that hit neither level make up the remainder and close at the end of the day.

The simple rules hardly change this. The best of the 48 rows is majors with previous-day momentum at 3x: +0.04% mean return and −0.18% log growth per window. This is indistinguishable from zero, and it was selected in-sample.

### 3. Why the window game loses

- **Costs scale with leverage.**
  - Each window pays the taker fee on both sides: 0.1% × L of equity, which is 0.5% at 5x.
  - A stopped window also pays slippage: another 0.5% of equity at 5x.
  - Log growth falls as leverage rises in every universe.
- **Costs move both levels against the trader.**
  - At 5x, the target needs a +2.10% price move, while the stop sits only −1.70% away.
  - Within a 24-hour window, the nearer level is hit disproportionately often. On majors at 2x, only 38% of the windows that hit a level hit the target.
- **The game needs a real edge.**
  - If every window ended at a level, zero growth would need 49.7% of windows at the target.
  - The no-edge baseline reaches 38–44% of decided windows at 2–5x.
  - So a signal must add roughly 6–12 percentage points of accuracy in a single daily call. Few signals do that.

### 4. Compounding the baseline

The Monte Carlo below draws window returns from the coin-flip rows. The project pause triggers when equity falls below 70% of the start.

| Universe, leverage | P(pause within 30 windows) | P(equity doubles within 30 windows) |
|---|---|---|
| Majors, 3x | 45% | 0.8% |
| Top 20, 3x | 58% | 2.1% |
| In play, 3x | 70% | 2.6% |
| Top 20, 5x | 70% | 1.3% |
| In play, 10x | 96% | 0.04% |

The compounding ladder (100 → 110 → 121 → …) is a streak. At a 50% chance of reaching the target in each window, 7 target windows in a row has a probability of 0.78%.

## Implications for the project

1. **The +10% stays a cap, not the per-trade objective.**
   - The window rules (lock gains at +10%, stop at −9%) remain risk rules.
   - Going all-in on one daily direction at leverage is a losing game without an edge, and this study shows how much it loses.
2. **Strategies need asymmetric trades.** Phase 3 should test trades that:
   - risk 1R and aim for 2R or more, or trail their stop;
   - enter intraday on a signal, not at a fixed time;
   - need a win rate around 35–40% after costs, not 50%.
3. **Costs are a first-order effect.** Use maker entries where possible, few trades, and no high-leverage churning. The risk configuration already caps effective leverage at 5x.
4. **Each universe has a cost.**
   - In-play symbols have the volatility, but also the worst no-edge results: more stop-outs and violent wicks.
   - Majors are the cheapest to trade, but rarely reach +10% at moderate leverage.
   - Strategy research should cover both the top-20 and in-play universes.
5. **Set expectations by the evidence.** The realistic research goal is a strategy with positive expectancy after costs out-of-sample. The +10% per window will come, if at all, from such an edge and favorable days, not from the structure of the rules.

## Limitations

- **Bar resolution.** With 5-minute bars, a bar that touches both levels counts as a stop. This matters only for in-play symbols at high leverage: 18% of windows at 10x and 4.5% at 5x, against under 1% at 2–3x. The 10x in-play rows are biased against the target.
- **Fills.** Stops fill at the stop price plus 0.1% slippage unless the bar gaps. In real squeezes, stop-market fills can be much worse. For example, BABYUSDT printed a high of 100 times its open on 2026-06-05. So the losses on in-play shorts are understated.
- **Funding** is not included. At 5x and a 0.01% rate every 8 hours, funding costs about 0.15% of equity per window. In-play symbols often pay much more.
- **One entry time.** Every window opens at 00:00 UTC.
- **One year of data.** The results are specific to one market regime.
- **Universe churn.** The previous-day volume ranking changes often: 399 different symbols entered the top 20 over the year.
