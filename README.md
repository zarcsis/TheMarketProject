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

**Phase 0: setup and safety.** Implemented so far:

- `mb check`: read-only checks of connectivity, API key safety, and account setup;
- `mb keygen`: generates an Ed25519 key pair for the Binance API.

See the [plan](docs/PROJECT_PLAN.md) for the phases ahead.

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

## Development

```sh
uv run pytest
uv run ruff check
uv run ruff format --check
```

## License

[MIT](LICENSE)
