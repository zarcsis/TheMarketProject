# Research 13: Out-of-time check

Status: running since 2026-10-22; re-run with `uv run mb research forward`. Latest table: [results/13-out-of-time.md](results/13-out-of-time.md). Code: [`research/forward.py`](../src/marketbot/research/forward.py).

## Question

Research 03 to 09 chose the event strategies on 2024-10 to 2026-09 data. Do they keep working on events after that?

This is the cheapest out-of-sample evidence there is: it needs no positions. It complements the Demo Trading forward test and the live trades.

## Method

- **Events since 2026-10-01**, taken from the same live sources the bot uses:
  - the announcements catalogs: delistings, Monitoring Tag, and airdrops;
  - exchangeInfo: new crypto perpetuals.
- **Simulation.** Each strategy runs with the bot's own settings, imported from its configuration:
  - entry at the first 5-minute bar after the strategy's delay;
  - stops checked on 5-minute highs, with slippage;
  - the strategy's holding time;
  - taker fees and funding from the public API.
- **Open trades.** A trade still within its holding time is marked at the last close and counted separately.

## Results so far (2026-10-01 to 2026-10-22)

| Strategy | Closed trades | Mean with funding | Research estimate |
|---|---|---|---|
| `delisting-short-4h` | 3 | +11.7% (3 wins) | about +5% |
| `monitoring-short-1d` | 2 | −3.3% (0 wins) | +3.5% to +6.8% |
| `listing-short-busy-7d` | 1 | +34.3% | +2% to +8% |
| `airdrop-short-7d` | 0 | — | about +10% |
| `exchange-delisting-short-7d` | 1 (and 1 open) | +17.5% | about +4.4% |

Events on perpetuals that were not tradable are skipped, as the bot skips them. That means under 100,000 USDT of volume in the day before, or no Binance perpetual at all. In October that left out QUICK's delisting and two Bybit delistings.

**Seven closed trades say little.** The delisting short agrees with its research, as it did in research 04's own October check. The two Monitoring Tag events came from one announcement and both lost.

## How to use it

- **Re-run monthly**, or after any strategy event, and update this table.
- **Then compare three numbers** for each strategy: the out-of-time mean, the research estimate, and the demo and live fills.
- **A strategy that keeps falling short of its research estimate** over 10 or more out-of-time events should be reviewed before it is scaled up live.
