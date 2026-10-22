# Research 17: KuCoin delistings, a replication

Status: complete, 2026-10-22. Partial replication of research 14. Full tables: [results/17-kucoin.md](results/17-kucoin.md). Code: research 14's, with `mb research exchanges --kucoin`.

## Question

Research 14 found that the Binance perpetual of a token drifts down for days after Bybit or OKX delist their own perpetual of it. That result was found on the data it was chosen from.

KuCoin also announces perpetual delistings, through a public API with history since 2024. Those events played no part in choosing the strategy. Does the effect hold on them?

## Method

- **Events.** KuCoin Futures delistings of USDT perpetuals since 2024-10, for tokens with a Binance crypto perpetual: 97 in all. Bitget's API returns only its latest notices, so it could not be used.
- **Kinds.** As in research 14, with one addition:
  - **Fresh:** the replication sample. 30 tradable events, 25 of them in the second year.
  - **After Binance:** Binance announced its own delisting of the token within the 30 days before (34 events).
  - **After Bybit/OKX:** research 14's strategy would already hold the short (11 events).
- **Trade.** Research 14's models, with the same filters.

## Findings

**The drift is there.** Without a stop, the fresh events fall as research 14's did:

| Short, with funding | First year (4–5) | Second year (25) |
|---|---|---|
| 3 days, no stop | −0.3% | +11.4% (t 3.6) |
| 7 days, no stop | −2.2% | +13.5% (t 2.8) |
| 7 days, 30% stop | −12.7% | +4.1% (t 0.8) |
| 7 days, 20% stop | −10.9% | +5.9% (t 1.2) |

**The stops hurt more than on Bybit and OKX.** These tokens had already fallen 13.6% on average in the 24 hours before the announcement. They often bounce more than 30% before falling: 20% to 32% of the stopped variants were stopped out within 7 days.

**With the demo strategy's settings** (7 days, 30% stop), the 29 events average about +1.8%, against +4.4% in research 14.

**There is no reaction at the announcement:** +0.5% at 5 minutes.

## How to read this

- **The direction replicates; the size with a stop does not.** The sample is small, and almost all of it comes from one year.
- **Taken together,** the Bybit, OKX, and KuCoin events with a 30% stop average about +3.5% per trade over 90 events.

## Implications

- **`exchange-delisting-short-7d` stays as it is:** Bybit and OKX triggers, Demo Trading only.
- **KuCoin is not added as a trigger.** Its events are weaker with a stop, and a third of them follow a Bybit or OKX delisting anyway.
- **A wider stop does not help.** A 40% stop gave +2.7% in the second year and −15% in the first.
