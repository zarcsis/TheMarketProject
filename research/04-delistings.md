# Research 04: Delisting announcements

Status: complete, 2026-10-22. Full table: [results/04-delistings.md](results/04-delistings.md). Code: [`research/delistings.py`](../src/marketbot/research/delistings.py), [`data/announcements.py`](../src/marketbot/data/announcements.py).

## Question

When Binance announces a delisting, its holders must exit before the delisting date. Does the token's USD-M perpetual keep falling after the announcement, so that a bot reading announcements can short it?

## Method

**Announcements.** The study uses Binance's public announcements endpoint, catalog 161 (delistings). It covers 443 articles from 2022 onwards. Symbols are parsed from two kinds of title:

- spot notices: "Binance Will Delist A, B, C on …";
- futures notices: "Binance Futures Will Delist XUSDT …".

**Events.** There are 79 since 2024-10 with 5-minute perpetual klines around the announcement, from 35 announcements.

**Entry and trade.** The entry is the open of the first 5-minute bar at least 5 minutes after the announcement. The trade is a short held 1 hour to 7 days:

- with no stop, or a stop 10% or 20% above the entry;
- with a 0.05% taker fee per side and 0.2% stop slippage.

## Findings

### 1. The first crash happens before a bot can act

Average price path relative to the first bar after the announcement:

| Offset | Mean | Median |
|---|---|---|
| −24 hours | +14.6% | +11.4% |
| −1 hour | +8.7% | +9.1% |
| 0 (entry reference) | 0 | 0 |
| +2 hours | −2.4% | −3.7% |
| +6 hours | −3.8% | −7.7% |
| +24 hours | −5.7% | −9.1% |

The drop between −1 hour and 0 happens within minutes of publication. The release timestamp falls a median 9 seconds into a 5-minute bar. That bar falls 5.3% on average (median 7.8%), while the 55 minutes before it are flat. That move belongs to the fastest traders.

### 2. The continuation after the first crash is tradable

Short from 5 minutes after the announcement, 20% stop, mean per trade:

| Hold | Events 2024-10 to 2025-09 (30 events, 18 announcements) | Events 2025-10 to 2026-09 (49 events, 17 announcements) | t (events) | t (announcements) |
|---|---|---|---|---|
| 4 hours | +8.4% | +4.9% | 4.35 | 3.23 |
| 1 day | +8.5% | +4.5% | 2.78 | 1.75 |
| 3 days | +10.1% | +5.9% | 2.84 | 1.57 |
| 7 days | +13.4% | +10.4% | 3.41 | 1.91 |

**The stop matters.** Without it, the 7-day mean is −33%: delisted tokens sometimes squeeze violently.

**The 4-hour hold is the most consistent,** and it fits within one trading session.

## Implications

- **This is the strongest result so far,** but the events are rare: about 1.5 announcements a month, each naming 2–3 tokens.
- **The live strategy is `delisting-short-4h`.** It polls the announcements every minute, enters 5 to 30 minutes after an announcement, and closes after 4 hours with a 20% exchange-side stop.
- **Expected contribution.** At 3% risk per trade, a 20% stop means a position of about 15% of equity, so +5% per trade adds about 0.75% of equity per event. That is worthwhile, but nowhere near 10% a day, and only on days with announcements.

## Limitations

- **Sample size.** It is small: 35 announcements, and events within one announcement are correlated.
- **Exchange restrictions.** Binance may restrict opening positions in a contract being delisted. A futures delisting notice usually sets a deadline, and the bot then cannot enter.
- **Liquidity.** Delisted tokens are illiquid, so real entry slippage can exceed the 0.02% the simulation assumes for entries.
- **Data coverage.** The website endpoint is not an official API, and the monthly archive does not yet cover announcements from the current month.
