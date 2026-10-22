# Overnight report: 2026-10-21/22

Covers from 22:00 to 09:00 local time (UTC−3). The owner asked for Phase 3 (strategies) and then a trial run, on historical data and on Demo Trading, and for live trading if ready.

## Summary

1. **Live trading was not started.** Claude does not place real-money orders or move funds, even with the owner's permission. [docs/LIVE.md](../LIVE.md) has the exact steps for the owner to start.
2. **The research ran on two years of Binance USD-M data** (2024-10 to 2026-09, delisted symbols included). Technical intraday rules do not beat costs. Three structural, event-driven shorts do, in both years:
   - after a delisting announcement;
   - after a Monitoring Tag announcement;
   - after a busy new listing.
3. **Expectations, honestly.** In-sample, the three event strategies together simulate to about ×3.7 in two years (about +0.2% a day) with a 14% realized drawdown. That is an upper bound. The +10% per day goal is not supported by anything found.
4. **The event strategies are implemented live and drilled end to end on Demo Trading.** The forward test is running on Demo Trading.

## What was built

**Backtester.** Trades on 5-minute bars with fees, slippage, gap fills, and stop-first ambiguity. Trailing and break-even exits apply from the next bar on.

**Live strategy runtime.**

- Strategies propose actions; the engine applies them through the risk manager. Entries are sized or rejected, stops only tighten, and a stop already through the market becomes a close.
- `mb auto on|off` switches the strategies on and off.
- Presets are selected with `MB_STRATEGIES`.

**Event strategies:**

| Strategy | What it does | Research |
|---|---|---|
| `delisting-short-4h` | Polls Binance announcements; shorts 5–30 minutes after a delisting notice; 20% stop; closes after 4 hours. Takes the most liquid tokens first. | 04 |
| `monitoring-short-1d` | The same, for the Monitoring Tag; holds 1 day. | 07 |
| `listing-short-busy-7d` | The day after a busy crypto listing (at least 50M USDT on the listing day), shorts with a 40% stop and closes after 7 days. | 03 |

**Option to keep positions between sessions** (`MB_ON_STOP=keep`). When the session ends, the bot makes sure every position has its exchange-side stop and leaves it open. DEFENSE, HALTED, and the project pause still close everything.

**Data:**

- Binance announcements (listings, delistings, news) from the public website endpoint;
- funding history;
- 1-minute and 5-minute klines around events.

**`mb market`.** A market brief from public data: trend, Fear & Greed, funding extremes, movers, listings, delisting notices.

**Robustness.** Handled events are remembered across restarts. TradFi contracts (stocks, commodities, FX) are excluded everywhere.

## Research results

| # | Question | Result |
|---|---|---|
| [02](../../research/02-strategies.md) | Breakouts, fades, opening range: 208 variants on two independent years | **No edge.** In-sample winners fail out-of-sample. |
| [03](../../research/03-listings.md) | Short new listings | **Positive in both years**, after funding and with a BTC hedge. The busy 7-day short makes +2.3% and +8.3% per trade. Works for entries until midday UTC. Busy crypto listings became rare in 2026. |
| [04](../../research/04-delistings.md) | Short after delisting announcements | **The strongest result.** About +5% per trade on 1-minute bars, t 2.7 over 35 announcements, positive for spot and futures delistings. October 2026, after the data: +8.3% over 4 events. |
| [05](../../research/05-spot-listings.md) | Long after spot listing announcements | **Negative.** The pump is over before a bot can act. |
| [06](../../research/06-funding.md) | Trade extreme funding | **Negative.** Squeezes are rare; signs flip between years. |
| [07](../../research/07-monitoring-tag.md) | Short after the Monitoring Tag | **Positive in both years.** +6.0% and +3.6% per trade on a 1-day hold, t 2.2 over 19 announcements. |
| [08](../../research/08-portfolio.md) | All event strategies under the risk rules | About ×3.7 over 703 days, 14% realized drawdown; 2026 alone about +55% annualized. In-sample upper bound. |

**Two of my own mistakes were caught along the way and corrected in the reports:**

1. **TradFi contracts.** 232 of them first polluted the listing study. Excluding them made the effect stronger, not weaker.
2. **A biased portfolio simulation.** An early scratch version broke ties between simultaneous events worst-first. Research 08 uses random order.

## Demo Trading

**Event strategies, end to end.** A synthetic delisting and a synthetic listing drove the real engine against Demo Trading ([script](../../scripts/drill_event_strategies.py)):

- **Delisting short:** 228 DOGE short with a +19.5% stop, closed by its time exit.
- **Listing short:** 6.9 XRP short with a +39.4% stop.
- **Sizing:** 15% and 7.5% of equity, as designed.

**Forward test.** It has run since about 01:20 UTC with all strategies, including the Donchian variants, which run only to exercise the pipeline. At the time of writing it had made no strategy trades: there were no announcements or busy listings overnight, and no breakout signals. The final state is added below in the morning.

## Market snapshot (01:44 UTC, `mb market`)

- **BTC:** about 83,750, flat (7d +1.9%, 30d −2.8%), slightly above its 50-day average.
- **ETH:** weaker (30d −10.7%).
- **Fear & Greed:** 68 (Greed).
- **Funding:** about neutral (median +0.005%). Crowded shorts on BIRB (−1.05%) and MEW (−0.86%).
- **Events:**
  - no new crypto perpetuals in the past week;
  - a futures delisting of PTBUSDT and MITOUSDT announced on 10-21. It was outside the entry window, but the 4-hour short made +11% and +14%.

## Decisions for the owner

1. **Whether to start live,** and with which strategies. The delisting short is the most robust. See [LIVE.md](../LIVE.md).
2. **Whether to allow multi-day positions between sessions** (`MB_ON_STOP=keep`). Without it, the listing and Monitoring Tag shorts cannot run as researched.
3. **The risk per trade at the start.** Suggested: `mb risk-scale 0.25` to `0.5` until live fills confirm the simulation.
4. **Funding the futures wallet** (Spot → USDⓈ-M Futures). This is an owner action.

## Next

- **Keep the forward test running on Demo Trading** toward the Phase 4 gate: 5 session-days without rule violations.
