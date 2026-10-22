# Research 14: Bybit and OKX announcements

Status: complete, 2026-10-22. One new candidate: `exchange-delisting-short-7d`, on Demo Trading only. Full tables: [results/14-exchanges.md](results/14-exchanges.md). Code: [`research/exchanges.py`](../src/marketbot/research/exchanges.py), [`data/exchanges.py`](../src/marketbot/data/exchanges.py).

## Question

News moves prices, but a bot that polls once a minute loses the race for fast news (research 05 and 10). The news it can use is news that starts a slow process.

Bybit and OKX announce listings and delistings through free public APIs with years of history. Do those announcements move the token's Binance perpetual over the following hours and days?

## Method

- **Announcements.** Bybit (`new_crypto`, `delistings`) and OKX (`new-listings`, `delistings`) since 2024-10, fetched with `mb data announcements --source exchanges`.
- **Kinds.** Spot listing, perpetual listing, spot delisting, perpetual delisting.
  - Notices that name no token are skipped, for example "Delisting of Selected Perpetual Contracts" or "Bybit to Delist 1 Token(s)". Bybit has used that format for most spot delistings since mid-2026.
- **Events.** Tokens with a Binance crypto USDT perpetual (not TradFi).
- **Two exclusions:**
  - **Not tradable.** The Binance perpetual traded less than 100,000 USDT in the 24 hours before. This was 79 events, usually because Binance had already delisted it: Bybit often delists after Binance.
  - **After Binance.** Delistings within 30 days after a Binance delisting announcement for the same token are counted apart. The perpetual's fate is already known then, and research 04 covers it.
- **Trade.** Research 10's two models:
  - the event study: entry 5 minutes after the announcement, long and short;
  - the funded short model: holds of 1 to 14 days, stops of 20% to 40% or none, entry delays up to 4 hours, with funding.

## Findings

### 1. Bybit and OKX perpetual delistings: the Binance perpetual drifts down

61 events (54 Bybit, 7 OKX):

- **No reaction at the announcement** (−0.04% at 5 minutes). The move comes over days, so speed does not matter.
- **The token was already falling:** −5.6% in the 24 hours before.
- **Every one of the 32 funded variants is positive** (4 holds × 4 stops × 2 years):

| Short, with funding | First year (19) | Second year (42) | Both (61) |
|---|---|---|---|
| 3 days, no stop | +9.8% (t 3.5) | +2.9% (t 1.6) | +5.1% (t 3.3) |
| 7 days, 20% stop | +5.2% (t 1.1) | +5.2% (t 2.1) | +5.2% (t 2.3) |
| **7 days, 30% stop** | +5.0% (t 1.0) | +4.2% (t 1.5) | +4.4% (t 1.8) |
| 7 days, 40% stop | +5.0% (t 1.0) | +3.8% (t 1.2) | +4.2% (t 1.6) |
| 14 days, 30% stop | +6.6% (t 1.2) | +7.3% (t 2.2) | |

**Robustness.**

- **Both exchanges.** Over 3 days without a stop, Bybit gives +4.9% (t 2.9) and OKX +6.5% (t 2.2, 7 events).
- **Not only later Binance delistings.** In 4 events Binance announced its own delisting within the following week. Without them, the 3-day short gives +4.8% (t 3.0).
- **Monitoring Tag.** Tokens that already had Binance's Monitoring Tag (7 events) fell far more, +15% over 3 days. Without them the short still gives +3.8% (t 2.5).
- **Concentration.** Without the 3 best events, the 3-day short gives +3.8% (t 2.7). For the 7-day short with a 20% stop it is +3.1% (t 1.6).
- **Entry delay.** With a 1-hour delay the 7-day short with a 20% stop gives +5.5%. With a 4-hour delay it gives +3.6%.

### 2. Spot listings on Bybit and OKX: strong in one year only

50 events.

- **Longs lose:** −7.6% after 7 days.
- **A 7-day short with a 20% stop gains 7.9% with funding (t 2.4), almost all of it in the second year:** +13.8% (t 3.6) against +2.6% (t 0.5) in the first.
- Like the Upbit fade of research 10, it is not stable enough to trade.

### 3. Spot delistings and perpetual listings: nothing

- **Spot delistings (20 tradable events):** no reliable move.
- **Perpetual listings (94):** noise dominated by a few extreme squeezes.

### 4. Side check: research 04 and 07

The same tradability test on the Binance studies leaves out 3 of 79 delisting events and 5 of 90 Monitoring Tag events. These are perpetuals already delisted, which the bot skips anyway.

Without them the results improve slightly:

- delisting short (4 hours): +6.2% → +6.45%;
- Monitoring Tag short (1 day): +4.5% → +4.8%.

## How to read this

- **A moderate, broad effect.** It has the same strength as the busy-listing short of research 03: positive everywhere, with t of about 1 to 2 per year.
- **The likely mechanism:** Bybit and OKX drop perpetuals of tokens that are losing liquidity and attention. Their own perpetual's holders close or move to Binance, and market makers withdraw.
- **Many questions have been asked of these two years** (this is the 14th). The variant was therefore taken from the middle of the grid, not its best: 7 days with a 30% stop.

## Implications

- **New demo strategy: `exchange-delisting-short-7d`.**
  - **Trigger:** a Bybit or OKX announcement delisting their own USDT perpetual of a token.
  - **Trade:** short the Binance perpetual within an hour, 30% stop, 7-day hold.
  - **Skips:** perpetuals Binance has itself scheduled for delisting (exchangeInfo `deliveryDate`).
  - **Expected:** about +4.4% per trade, about 2.5 events a month.
  - **Status:** Demo Trading only, like `airdrop-short-7d`. With a 30% stop it needs a live risk scale of about 0.45 at the current account size.
- **No strategy on Bybit or OKX listings,** and never a long after one.
- **The out-of-time check (`mb research forward`) now includes it.** In October 2026, one closed event (SPORTFUN, +17.5%) and one open.
