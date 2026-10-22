# Research 15: Tokens under the Monitoring Tag, over weeks

Status: complete, 2026-10-22. Negative result. Full table: [results/15-distress.md](results/15-distress.md). Code: [`research/distress.py`](../src/marketbot/research/distress.py).

## Question

Research 07 shorts a token for one day after Binance adds the Monitoring Tag. Research 14 found that tagged tokens fell hard when Bybit or OKX also dropped them. Do tagged tokens keep underperforming for weeks, until Binance delists them or removes the tag?

## Method

- **Events.** 74 Monitoring Tag additions of tradable perpetuals from 18 announcements since 2024-10. This uses the corrected parser of research 07.
- **Trade.**
  - A short from the open of the second UTC day after the announcement, after research 07's 1-day trade.
  - Held 7, 14, 30, or 60 days, or until the perpetual stops trading. A delisted perpetual's last close counts as the exit.
  - With and without a 40% stop, after fees and funding.
- **Benchmark.** Altcoins fell over the period, so each short is paired with a long in an equally weighted index of liquid altcoin perpetuals. The excess is the short's return plus the index's.

## Findings

**Most tagged tokens do lag, but the average short loses.**

| Short, no stop, against the index | First year (23) | Second year (51) |
|---|---|---|
| 7 days | +10.1% | −8.1% |
| 14 days | +11.7% | −7.7% |
| 30 days | −14.7% | −6.7% |
| 60 days | −71.8% | −10.5% |

- **In 61% to 68% of events the short beats the index,** depending on the hold.
- **A few huge squeezes decide the average.** Tagged tokens are thinly traded, crowded with shorts, and sometimes pump many times over. The worst was ALPACA, tagged in March 2025: its 60-day short lost 16.6 times the position.
- **A 40% stop limits the damage but does not create an edge:** −0.6% to −6% at 30 and 60 days.
- **Delisting notices did come:** within 60 days for a third of the events. The crash they bring (research 04) does not make up for the squeezes.
- **Across tokens the 30-day excess is −9.2% (t −1.4),** with each token counted once.

## Implications

- **No long-horizon short of tagged tokens.** The edge after the Monitoring Tag stays where research 07 found it: in the first day.
- **This is a warning for any multi-day short of a distressed token.** Squeeze risk is the dominant term. The 7-day event shorts survive because their triggers (a delisting by another exchange, an airdrop, a busy listing) come with steady selling, and they have exchange-side stops.
