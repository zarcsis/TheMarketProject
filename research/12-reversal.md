# Research 12: Weekly reversal and momentum

Status: complete, 2026-10-22. Negative result. Full table: [results/12-reversal.md](results/12-reversal.md). Code: [`research/reversal.py`](../src/marketbot/research/reversal.py).

## Question

Research 02 tested intraday technical rules on single symbols. This study looks across symbols instead.

Do the week's biggest movers among liquid altcoin perpetuals keep going (momentum) or turn back (reversal) over the next week?

## Method

- **Universe.** Each day, the crypto USDT perpetuals listed for at least 30 days with an average quote volume of at least 20M USDT over the last 7 days. That is 128 on a median day, from 2024-10 to 2026-09.
- **Signal.** The return over the last 7 days, from the open 7 days ago to yesterday's close.
- **Legs.**
  - The 3, 5, or 10 biggest gainers and losers, each traded long and short.
  - Equally weighted, held 7 days from the open.
  - Taker fees on both sides and funding are included; there are no stops.
- **Statistics.** On cohorts starting on Mondays only, so weeks do not overlap: 99 weeks, split at 2025-10-01.

## Findings

**The sign flips between years.**

| Leg (5 names) | First year, per week | Second year, per week | Both years |
|---|---|---|---|
| Momentum: long gainers | +8.0% (t 1.5) | −4.0% (t −1.5) | +1.9% (t 0.6) |
| Reversal: short gainers | −8.2% (t −1.5) | +3.8% (t 1.4) | −2.1% (t −0.7) |
| Momentum: short losers | +0.5% | +3.8% (t 1.6) | +2.2% (t 1.1) |
| Reversal: long losers | −0.7% | −4.0% (t −1.7) | −2.4% (t −1.2) |

In the first year, gainers kept rising; in the second, they fell back. With 3 or 10 names the pattern is the same, and no leg has |t| above 1.7 over both years.

**The gainers leg is extremely volatile.** A few runaway pumps in a week dominate the means, which is also why a stopless short of gainers is dangerous.

## Implications

- **No cross-sectional strategy.** Neither reversal nor momentum is stable across the two years.
- **This joins the evidence of research 02:** generic price patterns on liquid perpetuals do not survive costs. The edges the project has found are all tied to specific Binance events.
