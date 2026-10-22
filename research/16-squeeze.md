# Research 16: Funding interval changes

Status: complete, 2026-10-22. Negative result for a strategy, useful as a risk check. Full table: [results/16-squeeze.md](results/16-squeeze.md). Code: [`research/squeeze.py`](../src/marketbot/research/squeeze.py).

## Question

When a perpetual's funding rate is pinned at the cap, Binance shortens its funding interval, for example from 4 hours to 1 hour. One side of the market is then badly crowded. Binance stopped announcing these changes individually in 2025, but they are visible in the funding history (`funding_interval_hours`) and live in `/fapi/v1/fundingInfo`.

Does the crowded side get squeezed?

## Method

- **Events.** 375 interval shortenings since 2024-10. Of these, 297 are studied: those with 5-minute klines and at least 1M USDT of volume in the 24 hours before.
- **Crowded side.** The side paying funding at the switch. A negative rate means the shorts are crowded (228 events). A positive rate means the longs are (69).
- **Trade.**
  - Long and short, 5 minutes after the first settlement on the new interval.
  - Held 1, 3, or 7 days, with a 20% or 30% stop or none.
  - After fees and funding.

## Findings

**Buying when the shorts are crowded has a positive mean and no edge.**

The 1-day long with a 30% stop gives +12.4% in the first year and +3.9% in the second. However:

- **the median trade loses 3.7%;**
- **the 5 best of 228 trades make 75% of the total.** Without them, the mean is +1.4%. With a 20% stop, the best 5 make 94% of the total;
- **with each symbol counted once,** the 1-day long with a 20% stop gives +4.8% (t 1.9).

This is a lottery on rare, giant squeezes. It extends research 06's finding on extreme funding: the sign of the mean depends on a handful of events. Its expectancy cannot be relied on.

**Shorting into crowded shorts loses everywhere.** Every hold and stop loses in both years, from −1% to −25% per trade. The first year's 7-day short without a stop lost 93%.

**Crowded longs (69 events, only 5 in the first year):** no reliable move either way.

## Side check: do the event shorts walk into crowded shorts?

Almost never. At entry, the perpetual's funding interval was shortened with a negative rate in:

- 0 of 79 delisting events;
- 2 of 79 Monitoring Tag events;
- 0 of 32 airdrop events;
- 1 of 61 Bybit and OKX delisting events.

A crowding filter would change nothing, so none is added.

## Implications

- **No squeeze strategy.**
- **Never short a perpetual whose interval was just shortened under negative funding.** The event strategies already avoid this in practice.
