# Research 05: Spot listing announcements

Status: complete, 2026-10-22. Negative result. Full table: [results/05-spot-listings.md](results/05-spot-listings.md). Code: the research 04 event study, run with the long direction.

## Question

The classic "Binance listing pump": a Binance announcement that it will list a token on spot lifts the price. If the token already has a USD-M perpetual, can a bot buy the perpetual after the announcement and ride the pump?

## Method

- **Announcements.** "Binance Will List Name (TICKER)" titles from catalog 48 since 2024-10.
- **Events.** 27 where the token's USDT perpetual traded for at least a day before the announcement.
- **Trade.** Entry and costs as in research 04: the open of the first 5-minute bar at least 5 minutes after the announcement, with taker fees and stop slippage.
- **Both sides tested:** long, and short.

## Findings

**The pump is over before a bot can act.**

- The price is up 21% on average over the 24 hours to the entry, mostly at the announcement itself.
- The reaction from the last close before the announcement to the entry 5 minutes later is −0.8%.

**Longs lose.** After the entry the token gains for about an hour (+2.8% mean, +1.4% median) and then fades:

| Long hold | Mean | Median |
|---|---|---|
| 4 hours | +1.2% | −1.4% |
| 3 days | −5.3% | −9.7% |
| 7 days | −6.8% | −20.9% |

**Shorts are weak.** Sell-the-news shorts held 1 to 3 days with a 20% stop are positive in both years. But they rest on 8 and 19 events, with t-statistics of at most 1.4.

## Implications

- **No long strategy on spot listing announcements.** The edge belongs to whoever reads the announcement first.
- **The fade after the pump is the same supply effect as in research 03.** Research 03 already captures it on a much larger sample, so no separate strategy.
