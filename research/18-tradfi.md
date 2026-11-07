# Research 18: US-stock perpetuals around the NYSE open

Status: complete, 2026-11-07. Negative result. Full tables: [results/18-tradfi.md](results/18-tradfi.md). Code: [`research/tradfi.py`](../src/marketbot/research/tradfi.py).

## Question

Binance now lists far more perpetuals on stocks than on crypto tokens. Of the 24 USDT perpetuals listed in October 2026, 22 were on US stocks (TradFi perpetuals), one was on a crypto token, and one was a pre-market contract. The crypto listing strategies (research 03 and 09) have little left to trade.

TradFi perpetuals trade around the clock, while the stock exchange is closed for 17.5 hours each weeknight and over weekends and holidays. Off-hours prices come from crypto traders alone, with no stock market to anchor them.

Does a big off-hours move turn back after the NYSE open, so that a trade against it at the open earns?

## Method

- **Universe.** Binance's USDT perpetuals on US stocks: 190 with data, from 2026-02 to 2026-10, most of them listed since May. The first two days after a listing are skipped.
- **Calendar.** NYSE regular sessions, 09:30 to 16:00 New York time, with daylight saving time and the 2026 holidays. That gives 187 trading days.
- **Prices.** 5-minute klines from data.binance.vision. The price at an instant is the close of the bar that ends there.
- **Off-hours move.** From the perpetual's price at the previous session's close to its price at the open. Moves over a weekend or holiday are counted apart from weeknights.
- **Trade.**
  - At the open: short after a rise of 3%, 5%, or 8% or more; long after a drop of the same size.
  - Exit 30 or 60 minutes later, or at the close.
  - Taker fees of 0.05% on both sides. Funding is negligible: the median funding rate of these perpetuals is zero.
- **Liquidity.** Only perpetuals that traded at least 1M USDT in the previous session: 8,432 of 15,082 perpetual-days.
- **Returns.** Raw, and in excess of the day's mean over all the perpetuals.
- **Statistics.** t clusters by day. Big moves come in groups: one sector moves on the same news. Of the 408 weeknight rises of 5% or more, 56% fall on just 10 days.

## Findings

**1. Weeknight rises fade a little after the open, but not reliably.**

| Weeknight, short after a rise, net of fees | Events | Days | 30 min | 60 min | To the close |
|---|---|---|---|---|---|
| +3% or more | 847 | 106 | +0.44% (t 0.9) | +0.35% (t 0.7) | −0.03% |
| +5% or more | 408 | 83 | +0.57% (t 0.7) | +0.50% (t 0.6) | −0.07% |
| +8% or more | 148 | 42 | +0.41% (t 0.3) | +0.14% (t 0.1) | −0.76% |

- The means are positive, but each day's group of movers moves together.
- Counted per event, the +5% row would show t 2.6. Clustered by day it is t 0.7.
- By the close the fade is gone.

**2. Weeknight drops do not turn back.** The long after a drop of 3% to 8% averages −0.1% to +0.3% at 30 minutes after fees, with |t| below 0.4.

**3. Weekends and holidays: rises tend to continue, on too few days to tell.**

- **Short after a weekend rise.** Shorting a rise of 8% or more loses 1.9% over 60 minutes (t −2.3). That rests on 35 events over 13 weekends.
- **Multiple testing.** The tables test 60 cells, so one cell with |t| above 2 is what chance alone gives.
- **The reverse trade.** A long after a weekend drop of 8% or more has 24 events on 6 days.

**4. The off-hours move barely predicts the move after the open.** The pooled slope of the move after the open on the off-hours move is:

- on weeknights, −0.04 to 30 minutes and +0.05 to the close;
- on weekends, about −0.01.

So a 5% off-hours move is followed on average by a 0.2% move back: about what the fees and the spread at the open take.

## Implications

- **No strategy.** Off-hours trading in Binance's stock perpetuals does not overreact in a way that a trade at the open can collect after costs.
- **This joins research 02 and 12.** Generic price patterns do not survive the costs, while the edges the project has found are tied to specific announcements.
- **The weekend continuation is worth a recheck** when twice as many weekends have passed: `uv run mb research tradfi --end <last archived day>`.
- **Fewer crypto listings.** That Binance now lists mostly stock perpetuals is itself a finding for the plan. The listing-based strategies will see fewer events, while delistings, Monitoring Tags, and Bybit and OKX delistings carry on.
