# Research 10: Upbit announcements

Status: complete, 2026-10-22. Negative result for a new strategy. Full tables: [results/10-upbit.md](results/10-upbit.md). Code: [`research/upbit.py`](../src/marketbot/research/upbit.py), [`data/upbit.py`](../src/marketbot/data/upbit.py).

## Question

Upbit is the largest Korean exchange, and its KRW listings are known for moving prices everywhere. Many tokens it lists already have a Binance perpetual. Upbit announces listings, delistings, and warnings ("designated for investment caution") through a public API.

- Does the Binance perpetual move after these announcements?
- Can a bot that reads them trade the move?

## Method

- **Announcements.** Upbit's "trade" notices since 2024-10, fetched with `mb data announcements --source upbit`. They are classified from the Korean titles:
  - **KRW listing:** a new listing in the KRW market, or a KRW market added to a listed token;
  - **other listing:** a listing in the BTC or USDT market only;
  - **delisting**, **warning**, and **caution call**, a softer warning.
- **Events.** Tokens with a Binance USDT perpetual that had traded for at least a day, including 1000x contracts such as 1000PEPEUSDT:

  | Kind | Events |
  |---|---|
  | KRW listing | 102 |
  | Other listing | 29 |
  | Delisting | 12 |
  | Warning | 26 |
  | Caution call | 14 |

  20 more events are left out as not tradable: the perpetual traded less than 100,000 USDT in the 24 hours before, usually because Binance had already delisted it. This filter was added with research 14, on 2026-10-22.

- **Trade.** Research 04's event study: entry at the first 5-minute bar at least 5 minutes after the announcement, long and short, with and without stops.
- **Second pass, for the kinds that showed a short effect.** The research 09 short model:
  - funding summed over the time held;
  - stops up to 40%;
  - entry delays of up to 4 hours.
- **Split.** By date at 2025-10-01.

## Findings

### 1. KRW listings: the price fades, but funding takes most of it

**The pump comes before a bot can act.** In the 24 hours to the entry the perpetual is up 32% on average. Of that, 14% comes in the 5 minutes after the announcement.

**Longs lose.** A 7-day long loses 12.7% on average (t −4.6); only 18% are positive.

**Shorts gain in price.** A 7-day short without a stop gains 12.5% before funding (t 4.5; 82% positive), positive in both years.

**Funding takes most of it.** Shorts in a pumped token are crowded, and they pay funding:

- the mean cost is 7.6% over 7 days, and the median is 2.6%;
- 41 of 102 events cost more than 5%, and the worst cost 57%;
- the rate at entry gives no warning: it was normal in almost every event, and turned negative only after the entry.

**After funding the edge is weak:**

| Short, 5 minutes after | Mean | t |
|---|---|---|
| 7 days, no stop | +4.8% | 1.6 |
| 7 days, 40% stop | +4.5% | 1.7 |
| 7 days, 20% stop | +1.7% | 0.7 |

### 2. Other kinds

- **Listings in the BTC or USDT market only (29 events).** The perpetual had already risen 13% in the 24 hours, but did not react to the announcement. A 7-day short without a stop earns +8.1% after funding (t 2.7). The second year has only 6 events.
- **Delistings (12 events).** The perpetual falls 6.1% at the announcement. A 7-day short gains 5.7% more before funding (t 2.1), but 11 of the 12 events are from one year, and a 20% stop leaves nothing.
- **Warnings (26 events).** A 7-day short earns +9.3% before funding (t 2.9), but only +5.0% after (t 1.5).
- **Caution calls (14 events).** A 7-day short without a stop gains 8.5% before funding (t 2.5), but with a 20% stop it loses 1.3%. The sample is too small to act on.

### 3. Side check: funding in the live strategies

Research 04 and 07 left funding out. Measured now on their events, it is negligible because those positions are short-lived:

| Strategy | Events | Mean funding cost | Worst |
|---|---|---|---|
| Delisting short, 4 hours | 75 | 0.08% | 2.5% |
| Monitoring Tag short, 1 day | 87 | 0.08% | 2.0% |

Their returns stand.

## Implications

- **No new strategy.** The Upbit KRW listing fade is real in price, but other traders crowd the short and funding charges for it. After funding it is about +4.5% to +5% per trade with t below 2, which does not meet the promotion criteria.
- **Never go long after an Upbit listing.**
- **Count funding in every multi-day short of a pumped token.** It can cost more than the move. Research 03 and 09 already include it.
- **An exit when funding turns extreme does not help.** It was tested afterwards, as an exploration on the same events (106, before the volume filter): close the 7-day short at the first funding settlement below −0.03% to −0.2% (per 8 hours). That leaves +0.1% to +0.7% per trade, against +4.3% to +4.6% when holding. Extreme funding comes during short squeezes, so the exit takes the loss and misses the later fade.
