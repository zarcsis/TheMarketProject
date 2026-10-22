# Research 07: Monitoring Tag announcements

Status: complete, 2026-10-22. Full table: [results/07-monitoring-tag.md](results/07-monitoring-tag.md). Code: the research 04 event study with a different parser.

## Question

Binance adds a "Monitoring Tag" to tokens at risk of delisting: low liquidity, weak projects. Many tagged tokens are delisted later; EPIC, QUICK, VIC, HFT, PYR, VANRY, ACX, ICX, SCRT, and STORJ in this sample. Do tagged tokens drift down after the announcement, like delisted ones (research 04)?

## Method

**Announcements.** "Binance Will Extend the Monitoring Tag to Include A, B & C on …" titles in Binance's news catalog (49). There are 27 since 2022 and 19 since 2024-10.

**Events.** 79 perpetuals with 5-minute klines around the announcement, from 18 announcements.

**Corrected on 2026-10-22.** The title parser had also counted tokens that lose a tag in the same notice. One example: "... to Include WAN, Remove the Monitoring Tag for GPS & MBL, and Remove the Seed Tag for BERA, BIO, ZK & ZRO". That added 11 wrong events. The figures below are after the fix; the result held, slightly stronger.

**Trade.** As in research 04:

- a short from the open of the first bar at least 5 minutes after the announcement;
- held 1 hour to 7 days, with no stop or a 10% or 20% stop;
- a 0.05% taker fee per side and 0.2% stop slippage.

## Findings

**The pattern resembles delistings, but is milder.** The price falls about 9% in the 24 hours to the entry, mostly at the announcement, before a bot can enter. Then it keeps drifting down.

Short with a 20% stop, mean per trade:

| Hold | 2024-10 to 2025-09 (26 events, 7 announcements) | 2025-10 to 2026-09 (53 events, 11 announcements) | t (18 announcements) |
|---|---|---|---|
| 4 hours | +5.2% | +2.3% | 1.69 |
| 1 day | +6.8% | +3.5% | 1.95 |
| 3 days | +6.7% | +1.2% | 1.05 |
| 7 days | +11.7% | +2.8% | 1.16 |

**The 1-day hold is the most consistent.** It is positive in both years, and 13 of 18 announcements are positive on average.

**Out-of-time check, October 2026.** One announcement, BICO and CVC on 10-09 07:00 UTC, came after the data ends. On live 5-minute klines, the 1-day short with a 20% stop made −0.5% and −6.1%. Two events prove nothing either way, but they do not confirm the effect. Treat this strategy as weaker than the delisting short.

## Implications

- **A second announcement-driven short.** The live strategy is `monitoring-short-1d`: a short 5 to 30 minutes after the announcement, with a 20% exchange-side stop, held 1 day.
- **Holding.** A 1-day hold outlives most sessions. Running it live means keeping positions between sessions (`MB_ON_STOP=keep`), or accepting a shorter hold (4 hours is weaker).
- **Overlap with delistings.** Tagged tokens often get a delisting announcement later. The two strategies use separate risk buckets, so a token can be shorted on each event.

## Limitations

- **Sample size.** Only 18 announcements, with correlated events within each.
- **A fixed delay.** Entries are 5 minutes after the release time, as in research 04; faster entries were not tested for this event type.
