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
  | KRW listing | 106 |
  | Other listing | 29 |
  | Delisting | 20 |
  | Warning | 33 |
  | Caution call | 15 |

- **Trade.** Research 04's event study: entry at the first 5-minute bar at least 5 minutes after the announcement, long and short, with and without stops.
- **Second pass, for the kinds that showed a short effect.** The research 09 short model:
  - funding summed over the time held;
  - stops up to 40%;
  - entry delays of up to 4 hours.
- **Split.** By date at 2025-10-01.

## Findings

### 1. KRW listings: the price fades, but funding takes most of it

**The pump comes before a bot can act.** In the 24 hours to the entry the perpetual is up 31% on average. Of that, 13.5% comes in the 5 minutes after the announcement.

**Longs lose.** A 7-day long loses 12.2% on average (t −4.6); only 17% are positive.

**Shorts gain in price.** A 7-day short without a stop gains 12.0% before funding (t 4.5; 79% positive), positive in both years.

**Funding takes most of it.** Shorts in a pumped token are crowded, and they pay funding:

- the mean cost is 7.4% over 7 days, and the median is 2.2%;
- 41 of 106 events cost more than 5%, and the worst cost 57%;
- the rate at entry gives no warning: it was normal in 100 of 106 events, and turned negative only after the entry.

**After funding the edge is weak:**

| Short, 5 minutes after | Mean | t |
|---|---|---|
| 7 days, no stop | +4.6% | 1.6 |
| 7 days, 40% stop | +4.3% | 1.7 |
| 7 days, 20% stop | +1.6% | 0.7 |

### 2. Other kinds

- **Listings in the BTC or USDT market only (29 events).** The perpetual had already risen 13% in the 24 hours, but did not react to the announcement. A 7-day short without a stop earns +8.1% after funding (t 2.7). The second year has only 6 events.
- **Delistings (20 events).** The perpetual falls 3.7% at the announcement. There is no reliable move after it.
- **Warnings (33 events).** A 7-day short earns +7.3% before funding (t 2.8), but only +4.0% after (t 1.4).
- **Caution calls (15 events).** Nothing.

### 3. Side check: funding in the live strategies

Research 04 and 07 left funding out. Measured now on their events, it is negligible because those positions are short-lived:

| Strategy | Events | Mean funding cost | Worst |
|---|---|---|---|
| Delisting short, 4 hours | 75 | 0.08% | 2.5% |
| Monitoring Tag short, 1 day | 87 | 0.08% | 2.0% |

Their returns stand.

## Implications

- **No new strategy.** The Upbit KRW listing fade is real in price, but other traders crowd the short and funding charges for it. After funding it is about +4.5% per trade with t below 2, which does not meet the promotion criteria.
- **Never go long after an Upbit listing.**
- **Count funding in every multi-day short of a pumped token.** It can cost more than the move. Research 03 and 09 already include it.
- **An exit when funding turns extreme does not help.** It was tested afterwards, as an exploration on the same events: close the 7-day short at the first funding settlement below −0.03% to −0.2% (per 8 hours). That leaves +0.1% to +0.7% per trade, against +4.3% to +4.6% when holding. Extreme funding comes during short squeezes, so the exit takes the loss and misses the later fade.
