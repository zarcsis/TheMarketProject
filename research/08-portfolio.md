# Research 08: The event strategies as a portfolio

Status: complete, 2026-10-22. Full table: [results/08-portfolio.md](results/08-portfolio.md). Code: [`research/portfolio.py`](../src/marketbot/research/portfolio.py).

## Question

What would the three event strategies have earned together, sized by the project's risk rules?

The strategies:

- busy listings: a 7-day short with a 40% stop ([03](03-listings.md));
- delisting announcements: a 4-hour short with a 20% stop ([04](04-delistings.md));
- Monitoring Tag: a 1-day short with a 20% stop ([07](07-monitoring-tag.md)).

## Method

**Data.** The trades of research 03, 04, and 07 from 2024-10 to 2026-09.

**Sizing.**

- Each entry risks 3% (or 1.5%) of current equity, so a position is the risk divided by the stop distance plus costs.
- At most 3 (or 5) positions are open, and at most 2 per strategy bucket.

**Simultaneous events.** Several events often start at once: one announcement names several tokens, and several listings share a day. Which of them get the free slots is decided at random, over 50 random orders.

**Equity** is updated when trades close, so drawdowns are understated.

**A bias found and fixed.** An earlier scratch version broke these ties by the trade's own result, worst first. It understated the portfolio, about +22% a year instead of about +100%. The numbers below use random tie-breaking.

## Findings

At 3% risk per trade, with at most 3 positions open:

| Strategies | Equity after 703 days (median) | 5th to 95th percentile | Annualized | Max realized drawdown |
|---|---|---|---|---|
| All three | ×3.83 | ×3.28 to ×4.77 | +101% | 14% |
| Delisting + Monitoring | ×2.11 | ×1.88 to ×2.32 | +47% | 7% |
| Delisting only | ×1.77 | ×1.62 to ×1.94 | +34% | 6% |
| Monitoring only | ×1.22 | ×1.13 to ×1.29 | +11% | 4% |
| Listing only | ×2.10 | ×1.81 to ×2.55 | +47% | 14% |

At 1.5% risk per trade, all three together reach ×1.99, which is +43% a year with a 7% drawdown.

The figures were updated on 2026-10-22, after a parser fix removed 11 wrong Monitoring Tag events (see research 07).

**With the airdrop short of [research 09](09-airdrops.md)** (32 more events; added on 2026-10-22), at 3% risk with at most 3 positions open:

- the four strategies reach ×4.22 (5th to 95th percentile ×3.32 to ×5.27), about +111% a year, with a 15% drawdown;
- the airdrop short alone reaches ×1.29.

It runs on Demo Trading only until forward events confirm it.

**2026 only** (249 days, the most recent regime): all three return ×1.35 (+55% annualized) with a 10% drawdown. Busy listings were far rarer in 2026 (34 events), so their contribution fell.

## How to read this

- **Upper bound.** These are in-sample numbers. The strategies were chosen after looking at the same two years, and seven questions were asked of the data. Only the delisting short has an out-of-time check: four events in October 2026, averaging +8.3% per trade.
- **The drawdowns are too low.** They are realized only, and they assume the simulated fills. Live fills in thin tokens will be worse.
- **Even the upper bound is far from the goal.** ×3.8 in two years is about +0.19% a day; the project's goal is +10% a day.
- **The gain is lumpy.** Most days have no event at all.

## Implications

- **Recommended live set:** the delisting short first, as the most robust, plus the Monitoring Tag short and the busy-listing short if positions may be kept between sessions (`MB_ON_STOP=keep`).
- **Start at a lower risk scale** until live fills confirm the simulated ones. At about 120 USDT of equity the floor is about 0.3 for a 20% stop: below it, positions fall under Binance's 5 USDT minimum (see docs/LIVE.md).
