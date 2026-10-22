# Research 06: Extreme funding rates

Status: complete, 2026-10-22. Negative result. Full table: [results/06-funding.md](results/06-funding.md). Code: [`research/funding.py`](../src/marketbot/research/funding.py).

## Question

Extreme funding signals a crowded side:

- strongly negative funding means many shorts, so a squeeze is possible;
- strongly positive funding means many longs, so a flush is possible.

Does trading against or with the crowd the next day pay after costs and funding?

## Method

- **Data.** Crypto USD-M perpetuals from 2024-10 to 2026-09, on days with at least 10M USDT of volume. Funding is normalized to 8 hours.
- **Signals.**
  - Negative extremes: the day's lowest rate at or below −0.3% (mild) or −1% (strong).
  - Positive extremes: the highest at or above +0.1% (mild) or +0.3% (strong).
- **Trades.** Long and short at the next day's open, held 1 or 3 days, with a 20% stop. Taker fees apply, and the funding paid or received over the hold is included.
- **Caveat on the t-statistics.** Consecutive signal days overlap, so the t-statistics are optimistic.

## Findings

**No variant is consistent across the two years.**

**Squeezes after strongly negative funding are real but rare.** Longs have a positive mean but a negative median, and the mean shrinks in the second year:

| Hold | First year: mean / median | Second year: mean / median |
|---|---|---|
| 1 day | +1.4% / −2.0% | +0.5% / −2.3% |
| 3 days | +3.6% / −3.5% | +0.2% / −4.2% |

**After strongly positive funding:**

- the 3-day long made +18.4% in the first year and −0.5% in the second, with a −14% median;
- shorts are negative or flat.

**Shorts into crowded shorts lose on average.** They win more than half the time (positive median), but a few squeezes outweigh the wins.

## Implications

No strategy. Extreme funding marks volatility, not direction. Its main use is as a warning:

- funding costs are high on such symbols, as already measured for new listings;
- squeezes are more likely, so stops must be wide.
