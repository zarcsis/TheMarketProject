# Research 03: Shorting new listings

Status: complete, 2026-10-22. Full tables: [results/03-listings.md](results/03-listings.md). Code: [`research/listings.py`](../src/marketbot/research/listings.py).

## Question

Newly listed tokens face steady selling: airdrop recipients, early investors, and market makers unload into new liquidity. Do new USD-M perpetuals drift down after listing, after costs and funding?

## Method

- **Listings.** 345 crypto USD-M USDT perpetuals first listed between 2024-10 and 2026-09, delisted ones included. Their listing day is the first day with daily klines.
- **TradFi excluded.** Binance's 232 TradFi and index perpetuals (stocks, commodities, FX, pre-market) are left out. They have no airdrop supply and diluted the effect when included.
- **Trade.** Short at the open of day 1 or 3 after the listing day and hold 1, 3, 7, or 14 days, with a stop 15%, 25%, or 40% above entry.
  - The stop is checked on daily highs, so a day that touches it counts as stopped.
  - A day that opens beyond the stop fills at the open, plus 0.2% slippage.
- **Costs.** A 0.05% taker fee per side, plus the actual funding paid or received over the holding days.
- **Also reported:**
  - the same trade hedged with a BTC long of equal notional;
  - a "busy" subset: listing-day volume of at least 50M USDT, a threshold fixed in advance.
- **Split.** By listing date: 2024-10 to 2025-09 versus 2025-10 to 2026-09.

## Findings

### 1. New listings drift down

The median daily return is negative on almost every one of the first 30 days after listing. Unhedged shorts from day 1 have a positive median that grows with the holding period: +3.5% after 3 days, +5.6% after 7, and +12.8% after 30.

**The mean is fragile.** A few listings rise several-fold, so a short without a stop has a poor mean. A wide stop is essential, while a tight stop (15%) is hit too often by ordinary volatility.

### 2. A day-1 short with a 40% stop is positive in both years

Mean return per trade:

| Listings | Hold | First year: with funding | First year: BTC-hedged | Second year: with funding | Second year: BTC-hedged |
|---|---|---|---|---|---|
| All | 3 days | +1.9% | +2.5% | +3.2% | +1.9% |
| All | 7 days | +2.1% | +3.5% | +7.2% | +5.0% |
| All | 14 days | +2.4% | +4.2% | +7.0% | +4.1% |
| Busy | 7 days | +2.3% | +3.8% | +8.3% | +6.0% |
| Busy | 14 days | +3.8% | +5.6% | +9.1% | +5.4% |

The t-statistics are 1.0 to 2.3 per year. Funding costs shorts about 0.1–0.3% per day in the first week, because new listings are crowded with shorts.

### 3. The portfolio return is modest

Sizing under the project's risk rules:

- risk is 3% of equity per trade, so a 40% stop means a position of about 7.5% of equity;
- at most 2 to 5 shorts are open at once;
- results compound on realized PnL over 685 days.

| Setting | Annual return | Drawdown |
|---|---|---|
| Typical settings | +25% to +40% | 15–35% |
| Range across settings | +12% to +128% | 14–52% |

The wide spread between close settings means the exact figures are noisy. Expect about 0.1% per day, not 10%.

### 4. Robustness: the entry hour

The live bot may enter hours after 00:00 UTC. The 7-day short with a 40% stop was re-simulated on 5-minute klines for every listing, entering on day 1 at different hours. The figures are before funding.

| Entry (UTC, day 1) | All listings | Busy, first year | Busy, second year |
|---|---|---|---|
| 00:00 | +4.9% | +3.6% | +9.5% |
| 04:00 | +5.2% | +4.4% | +10.0% |
| 08:00 | +5.2% | +3.9% | +10.4% |
| 12:00 | +3.6% | +3.5% | +5.4% |

**Validation of the daily model.** The 00:00 row matches the daily-bar model to the hundredth.

**The live entry window.** The edge holds for entries until midday UTC, which supports the strategy's window (entries until 12:00 UTC).

### 5. Robustness: quarters and concentration

The busy 7-day short with a 40% stop (with funding) by listing quarter:

| Quarter | Trades | Mean | Median | Win rate |
|---|---|---|---|---|
| 2024-Q4 | 36 | +3.1% | +12.4% | 64% |
| 2025-Q1 | 41 | +10.5% | +22.2% | 68% |
| 2025-Q2 | 53 | +2.6% | +8.2% | 64% |
| 2025-Q3 | 65 | −3.4% | +4.4% | 55% |
| 2025-Q4 | 54 | +10.5% | +20.0% | 69% |
| 2026-Q1 | 21 | +4.9% | +15.3% | 62% |
| 2026-Q2 | 9 | −1.3% | +22.2% | 56% |
| 2026-Q3 | 4 | +14.1% | +24.3% | 75% |

**Quarterly consistency.** Six of eight quarters have a positive mean, and the median is positive in every quarter.

**Concentration.** The 10 best trades make 55% of the total profit. Without them the mean is still +2.0% per trade.

**No market-regime filter is needed.** The busy 7-day short is positive whether BTC closed above or below its 50-day average the day before, in both years. With funding:

| BTC the day before | First year | Second year |
|---|---|---|
| Above its 50-day average | +2.8% | +10.3% |
| Below its 50-day average | +1.7% | +7.5% |

**Frequency is falling.** Busy crypto listings became rare in 2026, as Binance listed more TradFi contracts. Expect one to three trades a month, not the 10–20 of 2025.

## Implications

- **This is the first structural edge the project has found.** It is consistent across both years and survives funding and a BTC hedge.
- **The live strategy is `listing-short-busy-7d`.** It shorts busy listings the day after listing, with a 40% exchange-side stop and a 7-day hold.
- **Holding period versus the session rule.** The 7-day hold conflicts with "the session ends, trading stops, and everything is closed". Running it live needs one of two things:
  - sessions that last for days;
  - a decision to keep positions open between sessions, protected by their exchange-side stops.

## Limitations

- **Stop checks.** Daily-bar checks are conservative for stops but blind to intraday spikes beyond the slippage assumption. New listings can gap violently.
- **Listing-day entry.** The live entry may come hours after 00:00 UTC on day 1. Day 0 is excluded because its open is the listing price, not a tradable level for a bot that learns about the listing late.
- **Sample size.** It is moderate: 345 listings, and 283 busy ones.
