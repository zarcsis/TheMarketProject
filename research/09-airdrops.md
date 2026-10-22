# Research 09: Listing types and airdrop announcements

Status: complete, 2026-10-22. Full tables: [results/09-airdrops.md](results/09-airdrops.md). Code: [`research/airdrops.py`](../src/marketbot/research/airdrops.py).

## Question

Research 03 found that busy new listings drift down. The likely cause is supply: airdrop recipients, early investors, and market makers sell into new liquidity. Two questions follow:

1. **Which listings drift most?** Binance hands some tokens to its users before listing them (HODLer Airdrops, Launchpool, Megadrop). Others launch on Binance Alpha, on spot, or on futures only.
2. **Is the airdrop itself an event?** Since late 2025, Binance often lists the perpetual first and announces the HODLer Airdrop weeks later. Does the perpetual fall after that announcement?

## Method

### Part 1: listing kinds

- **Listings.** The 345 research 03 listings. The short is the same: entry on day 1, a 7-day hold, and a 40% stop, with funding.
- **Classification.** Each listing is classified from Binance announcement titles (catalogs 48 and 49). Only titles released before the entry count, so the class is known when the short opens. The first match wins:
  - **airdrop:** a HODLer Airdrop, Launchpool, or Megadrop of the token in the last 60 days;
  - **alpha:** a launch on Binance Alpha and Futures together in the last 60 days;
  - **spot:** a spot listing at any earlier time;
  - **futures only:** no title names the token, apart from the futures launch.

### Part 2: airdrop announcements on listed perpetuals

- **Events.** 32 HODLer Airdrops, Launchpool, and Megadrop announcements from 2024-10 to 2026-09 whose token's USDT perpetual had already traded for a day. There were 78 such announcements in all; the others came before the perpetual existed.
- **Trade.** Short at the first 5-minute bar at least 5 minutes after the announcement. Holds of 1, 3, 7, and 14 days, with stops of 20%, 30%, 40%, or none.
- **Costs.**
  - The fills, fees, and stop slippage are as in research 04.
  - Funding is summed over the time held.
  - A BTC-hedged variant adds a BTC long of the same notional.
- **Split.** By announcement date at 2025-10-01.

## Findings

### 1. Airdropped listings drift most

The research 03 short (day 1, 7 days, 40% stop), with funding:

| Kind | First year: trades | First year: mean | Second year: trades | Second year: mean |
|---|---|---|---|---|
| Airdrop | 36 | +10.9% (t 1.9) | 7 | +39.0% (t 4.3) |
| Alpha | 32 | +3.6% | 18 | +5.8% |
| Spot | 29 | +7.5% | 7 | −6.1% |
| Futures only | 142 | −1.5% | 74 | +5.7% (t 1.5) |

**Airdropped listings are positive in every variant.** That holds for all 12 combinations of hold and stop, with t from 1.6 to 3.6. Their median 7-day short earns +25%.

**Futures-only listings are close to zero.** They are the largest group, and pooled over both years their mean is between −2.8% and +1.0% per trade in every variant.

**But airdropped listings no longer reach the listing short.**

| Half-year | Listings | Airdrop | Alpha | Spot | Futures only |
|---|---|---|---|---|---|
| 2024-H2 | 54 | 6 | 0 | 15 | 33 |
| 2025-H1 | 115 | 19 | 9 | 13 | 74 |
| 2025-H2 | 125 | 18 | 40 | 2 | 65 |
| 2026-H1 | 43 | 0 | 1 | 5 | 37 |
| 2026-H2 (to September) | 8 | 0 | 0 | 1 | 7 |

In 2026 Binance lists the perpetual first. The airdrop, if any, is announced later, so at the day-1 entry nearly every listing is "futures only".

### 2. Shorting after the airdrop announcement

Short at the announcement, 7-day hold, 40% stop, with funding:

| Period | Events | Mean | Median | Win rate | t | Stopped |
|---|---|---|---|---|---|---|
| 2025-02 to 2025-09 | 18 | +2.9% | +6.6% | 56% | 0.3 | 33% |
| 2025-10 to 2026-06 | 14 | +19.6% | +20.2% | 86% | 2.6 | 7% |
| Both | 32 | +10.2% | +15.7% | 69% | 1.7 | 22% |

- **The sign holds across holds and stops.** Every hold and stop gives a positive mean in both periods, except a 3-day hold without a stop in the first year.
- **The 2026 events:** 7 of 8 were positive. The best was +42.6% (OPN) and the only loss was −23.4% (OPG).
- **No hurry.** Entering 1 hour after the announcement gives +9.0% per trade. Entering 4 hours after gives +13.7%, with t 2.3 over 34 events.
- **Stops matter.** Without a stop, three first-year events lost 100% or more within 3 days (BABY, 0G, XPL).
- **Concentration.** Without the 3 best events, the mean is +4.7%.

**Exploratory: the age of the perpetual.** The first-year losers were mostly perpetuals only a few days old, still in price discovery.

| Perpetual age at the announcement | Events | Mean |
|---|---|---|
| Under 7 days | 10 | +2.7% |
| 7 days or more | 22 | +13.6% (t 2.1) |

This split was chosen after seeing the data, so it is not used as a filter.

## How to read this

- **Same mechanism, two forms.** Supply handed out by Binance is sold over the following days. In 2024–25 that supply arrived together with the listing; in 2026 it arrives with the airdrop announcement, after the listing.
- **The samples are small.** There are 43 airdropped listings and 32 airdrop events. Part 2's first year alone is not significant.
- **Many questions have been asked.** This is the ninth question asked of the same two years, so the figures are an upper bound.

## Implications

- **No change to `listing-short-busy-7d`.** Excluding futures-only listings would remove almost every 2026 trade, and their second-year mean is positive.
- **Per-kind risk sizing is an option for later:** full risk for airdropped listings, less for futures-only ones. It is not worth adding while airdropped listings no longer appear at the entry.
- **New demo strategy: `airdrop-short-7d`.**
  - **Trade:** a short after a HODLer Airdrops, Launchpool, or Megadrop announcement for a token whose perpetual has traded for at least a day.
  - **Settings:** 7-day hold, 40% stop, entries up to 4 hours after the announcement. These are the listing short's settings, fixed before this study.
  - **Expected frequency:** about one event a month.
  - **Status:** it runs on Demo Trading only, as an out-of-time test. It is not a live candidate until forward events confirm it.
