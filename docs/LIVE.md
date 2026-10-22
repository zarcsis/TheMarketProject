# Live trading: how to start

Status: 2026-10-22. Nothing has traded live yet.

## Who does what

**The account owner starts and stops live trading.**

- Claude does not place real-money orders or move funds, even with permission. This rule binds Claude itself; it is not a setting of this project.
- So the account owner runs every command below that trades or transfers.

**Claude's role in a live session:**

- read the state (`mb status`, `mb events`);
- analyze markets and news;
- reduce risk with commands that do not trade (`mb pause`, `mb risk-scale`);
- explain what the bot did.

The owner runs `mb flatten` and `mb session stop`.

## What the research supports

| Strategy | Evidence | Live? |
|---|---|---|
| `delisting-short-4h` ([research 04](../research/04-delistings.md)) | Positive in both years. +4.9% to +8.4% per trade, t 3.2 over 35 announcements. Rare: about 1.5 announcements a month. | **Yes**, the first candidate |
| `listing-short-busy-7d` ([research 03](../research/03-listings.md)) | Positive in both years after funding and with a BTC hedge, t 1–2.3. Holds positions for 7 days. | Only with `MB_ON_STOP=keep` (see below) |
| `donchian24-*` ([research 02](../research/02-strategies.md)) | No reliable edge. | **No**, Demo Trading only |

**Simulated portfolio of both event strategies.** Over 2024-10 to 2026-09, at 3% risk per trade, the two event strategies return about +22% a year with a 29% realized drawdown. That is about 0.06% a day. The +10% per day target is not supported by any evidence found so far.

## Before the first live session

1. **Check the account:** `uv run mb check` must pass.
2. **Fund the futures wallet.** In the Binance app or website: **Wallet → Transfer → from Spot to USDⓈ-M Futures**, for example 100 USDT. The futures fee discount needs BNB in the futures wallet too, so you can optionally transfer some BNB as well.
3. **Close manual positions.** Anything you hold on USD-M futures will be treated as an unknown position and closed by the bot.

## Starting a session (PowerShell, in the repository folder)

**Terminal 1** is the session itself. Keep it open; closing it ends the session.

```powershell
$env:MB_STRATEGIES = "delisting-short-4h"
uv run mb session start
```

**Terminal 2** controls the bot:

```powershell
uv run mb status                 # equity, window, mode, open risk, positions
uv run mb risk-scale 0.25        # first sessions: a quarter of the normal risk per trade
uv run mb auto on                # let the strategies trade
uv run mb events --limit 30      # what happened
```

**Stopping:**

- `uv run mb session stop` closes everything and stops the bot.
- Closing terminal 1 has the same effect within about two minutes.

## Keeping multi-day positions between sessions (optional)

`listing-short-busy-7d` holds a position for 7 days. The default session rule (a session ends, everything closes) would cut every trade short. To keep its positions open between sessions:

```powershell
$env:MB_STRATEGIES = "delisting-short-4h,listing-short-busy-7d"
$env:MB_ON_STOP = "keep"
uv run mb session start
```

**When the session ends,** the bot makes sure every position has its exchange-side stop and leaves the positions open.

**Between sessions, nothing manages them.**

- The stop is the only protection, and the window rules are not enforced.
- The open-risk budget at entry still limits the total loss if every stop fires.
- The 7-day time exit happens at the next session after the 7 days have passed.

## What to expect

- **Most sessions will not trade.** Delisting announcements come about 1.5 times a month, busy listings a few times a week.
- **Position sizes are small.** At 3% risk per trade:
  - a delisting short (20% stop) is about 15% of equity;
  - a listing short (40% stop) is about 7.5% of equity.

## Known limits

- **Contracts being delisted.** Binance may block new positions in them; the bot then logs the rejection and skips the trade.
- **Announcement data.** It comes from Binance's public website endpoint, not an official API.
- **Thin markets.** Live fills in illiquid tokens can be worse than the simulation assumed.
