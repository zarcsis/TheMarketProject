# Research 11: Token unlocks

Status: complete, 2026-10-22. Negative result. Full tables: [results/11-unlocks.md](results/11-unlocks.md). Code: [`research/unlocks.py`](../src/marketbot/research/unlocks.py), [`data/unlocks.py`](../src/marketbot/data/unlocks.py).

## Question

Vesting schedules release tokens to teams and early investors on known dates. The common view is that prices fall into large unlocks as holders hedge or sell. Can a bot short a perpetual before a scheduled unlock and profit?

## Method

**Unlock schedules.** DefiLlama's public emission datasets for 372 protocols, fetched with `mb data unlocks`. Each lists cliff and linear unlocks by allocation, and the unlocked supply by day.

**Events.**

- **What counts:** cliff unlocks to insiders and private-sale investors of at least 1% of the circulating supply on the day before. Treasury and reserve allocations are left out of that supply.
- **Matching:** tokens are matched to Binance USDT perpetuals through their CoinGecko ticker.
- **Sample:** 595 events of 67 tokens, from 2024-10 to 2026-09, at most one per token and day.

**Trade.**

- **Entry and exit:** on daily bars, short at the open 14, 7, 3, or 1 days before the unlock day, and cover at the open 0, 1, 3, or 7 days after it.
- **Costs:** taker fees and funding.
- **No look-ahead:** schedules are published in advance, so the entry is feasible.

**Baseline.** Many of these tokens unlock every month, and altcoins fell over the period, so a short would earn something in any week. Each event is therefore compared with the same window on every day of the period for the same perpetual. The difference is the excess.

## Findings

**The excess is small.** Over all sizes, it is +0.5% to +2.2% depending on the window. The main variant, fixed in advance (7 days before to the unlock day's open), gives +1.35% (t 2.0).

**It comes from one year.** In the main variant the excess is −0.5% in the first year and +2.5% in the second. Most windows look the same.

**Tokens unlock repeatedly, so the events are not independent.** Treating each token as one observation leaves a +2.7% mean, but with t 1.2 across 67 tokens. That is not distinguishable from zero.

**Larger unlocks are not consistently worse.**

- Unlocks of 5–10% of supply gave −0.7% excess in the main variant.
- Unlocks of 10% or more gave +3.7% (t 1.1, 53 events).
- The best combinations of size and window would be data mining: 64 were tried.

**The unlock day itself:** from 1 day before to 1 day after, the excess is +0.9% (t 2.5), positive in both years. That is too small to pay for the stop a short in these tokens needs.

## Implications

- **No unlock strategy.** The effect, if any, is about 1% per event and not stable across years. Being public and scheduled, it is likely priced in.
- **Unlock data stays available for later work** (`mb data unlocks`), for example to avoid entering a long just before a large unlock.
