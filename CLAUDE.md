# CLAUDE.md

Guidance for Claude Code sessions in this repository.

## Project

TheMarketProject is a research trading bot for a small Binance account (about 100 USDT), operated from Claude Code sessions. The goals, risk framework, architecture, and phases are in [docs/PROJECT_PLAN.md](docs/PROJECT_PLAN.md). Read the plan before changing trading or risk code, and update it when a decision changes.

## Language

- Talk to users in English unless they ask for another language.
- Everything in the repository is in English: code, comments, documents, and commit messages.

## Public repository

This repository is public. Never commit, log, or print:

- API keys, private keys, `.env`, or anything under `secrets/`;
- the account UID, whitelisted IP addresses, or listen keys.

Raw logs and state belong in `data/`, `logs/`, and `state/`, which git ignores. Only curated reports go to `research/`.

## Safety rules

These are not negotiable.

- **One path to the exchange.** Every order goes through the risk manager. Do not place orders from ad-hoc scripts, the shell, or direct API calls. The only exception is the emergency `mb flatten` command.
- **Limits only tighten at runtime.** During a session, risk limits can be tightened but never loosened. Loosening a limit is a committed config change made outside a live session.
- **Protective stops.** Every position has an exchange-side stop.
- **Key safety.** The API key has no withdrawal permission and is IP-restricted. `mb check` must pass before any live trading.
- **Prohibited trading.** No market manipulation (pump groups, spoofing, wash trading), no martingale, no averaging down.
- **Live commands.** Run live trading commands only inside a trading session that the user has started.

## Commands

```sh
uv sync                        # install Python and dependencies
uv run pytest                  # tests
uv run ruff check              # lint
uv run ruff format             # format
uv run mb check [--public-only]
uv run mb keygen

# Trading sessions (add --env demo for Binance Demo Trading)
uv run mb session start        # blocking: run it as a background task; it is the session heartbeat
uv run mb status               # equity, window, mode, risk, positions, recent events
uv run mb events --limit 30
uv run mb propose SOLUSDT long --stop 105.2 [--target 111.6] --why "..."
uv run mb pause | resume | risk-scale 0.5 | flatten
uv run mb session stop         # closes everything, stops the bot
```

Stopping the Claude Code task that runs `mb session start` ends the session: within about two minutes the bot closes everything and exits.

## Conventions

- **Python 3.13+.** Use asyncio for I/O, `httpx` for REST.
- **Money.** Use `Decimal` for prices, quantities, and balances; never `float` in order math.
- **Separate logic from I/O.** Keep evaluation and decision logic in pure functions, apart from network code, so it can be unit-tested. Mock HTTP with `httpx.MockTransport`.
- **Workflow.** Commit directly to `main` in small, self-contained commits, and push after each completed step. CI runs ruff and pytest on Linux and Windows and must stay green.
