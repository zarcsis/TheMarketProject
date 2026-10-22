# TheMarketProject

**TheMarketProject** is an open experiment in AI-operated crypto trading. A Python bot trades a small Binance account (about 100 USDT). During interactive [Claude Code](https://claude.com/claude-code) sessions, Claude acts as its portfolio manager: it reads news and market structure, sets the regime and bias, chooses which strategies run, and throttles risk. The bot itself executes trades and enforces hard risk limits that Claude cannot loosen.

The daily goal is deliberately extreme: **+10% per 24 hours, compounding**. The risk rules are strict:

- **Target reached:** lock in the profit.
- **Loss beyond 9%:** stop trading for the day.
- **Session ends:** trading stops and all positions are closed.

> **Warning:** this is a research experiment, not financial advice. A +10% daily target is not sustainable, and the expected long-run outcome of high-leverage trading is a loss. The [plan](docs/PROJECT_PLAN.md) explains why the experiment runs anyway and how it is measured. Do not run this code with money you cannot afford to lose.

## What is public

Everything except secrets is published in this repository:

- the code;
- the [project plan](docs/PROJECT_PLAN.md);
- research notes, backtests, and session reports, including results and losses.

## Status

As of 2026-10-22: setup, the trading engine, and the strategy research are done, and the forward test on Binance Demo Trading is running. Nothing has traded with real money yet.

**Built:**

- **Risk manager.** Hard limits the strategies and Claude cannot loosen:
  - a 24-hour window with a +10% target and a −9% loss line;
  - profit locking above the target;
  - an open-risk budget;
  - a project-level pause.
- **Execution engine.** Every position has an exchange-side stop. The engine reconciles with the exchange and closes everything when the session ends. It was drilled on Binance Demo Trading: [report](docs/drills/2026-10-22-phase2-demo.md).
- **Backtester and live strategy runtime.** Live strategies run the same signal code as the backtests.

**Research, two years of Binance USD-M data, delisted symbols included:**

| # | Question | Result |
|---|---|---|
| [01](research/01-feasibility.md) | Is +10% a day reachable without an edge? | No. Every variant loses after costs. |
| [02](research/02-strategies.md) | Do intraday technical rules (breakouts, fades, opening range) beat costs? | No, in either year. |
| [03](research/03-listings.md) | Do new listings drift down? | Yes. Shorting busy listings for a week is positive in both years. |
| [04](research/04-delistings.md) | Do tokens keep falling after a delisting announcement? | Yes. A 4-hour short averages about +5% per trade. |
| [05](research/05-spot-listings.md) | Can a bot ride spot listing pumps? | No. The pump is over before a bot can act. |

**Realistic expectation.** The two event strategies together simulate to about +20% a year, not +10% a day. [LIVE.md](docs/LIVE.md) explains how the account owner starts live trading.

## Quick start

You need [uv](https://docs.astral.sh/uv/). It installs Python and the dependencies.

```sh
uv sync
uv run mb check --public-only   # connectivity, clock offset, symbol filters; no key needed
```

### Connect your Binance account

1. Generate a key pair:

   ```sh
   uv run mb keygen
   ```

   The private key is written to `secrets/`, which git ignores.

2. On Binance, open **API Management → Create API → Self-generated**, paste the public key, and set the permissions:

   | Permission | Setting |
   |---|---|
   | Reading | On |
   | Spot & Margin Trading | On |
   | Futures | On |
   | Universal Transfer | On |
   | Margin Loan, Repay & Transfer | Optional |
   | **Withdrawals** | **Off** |
   | **IP access restriction** | **On**, with the IP of the machine that runs the bot |

3. Copy `.env.example` to `.env` and fill in `BINANCE_API_KEY`. The private key path is already set.

4. Run the full check:

   ```sh
   uv run mb check
   ```

   It exits with a failure if withdrawals are enabled or the key is not IP-restricted.

## Running

```sh
uv run mb market                        # market brief from public data
uv run mb --env demo session start      # a session on Binance Demo Trading (blocking)
uv run mb --env demo status             # in another terminal
uv run mb research <study>              # feasibility, strategies, listings, delistings, ...
```

See [CLAUDE.md](CLAUDE.md) for every command and [docs/LIVE.md](docs/LIVE.md) for live trading.

## Development

```sh
uv run pytest
uv run ruff check
uv run ruff format --check
```

## License

[MIT](LICENSE)
