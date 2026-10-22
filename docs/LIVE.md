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
| `delisting-short-4h` ([research 04](../research/04-delistings.md)) | Positive in both years: about +5% per trade on 1-minute bars, t 2.7 over 35 announcements. An out-of-time check in October 2026 gave +8.3% over 4 events. Rare: about 1.5 announcements a month. | **Yes**, the first candidate |
| `listing-short-busy-7d` ([research 03](../research/03-listings.md)) | Positive in both years after funding and with a BTC hedge, t 1–2.3. Holds positions for 7 days. | Only with `MB_ON_STOP=keep` (see below) |
| `monitoring-short-1d` ([research 07](../research/07-monitoring-tag.md)) | Positive in both years: +3.5% to +6.8% per trade, t 1.95 over 18 announcements. Holds positions for 1 day. | Only with `MB_ON_STOP=keep`, or as a shorter hold |
| `airdrop-short-7d` ([research 09](../research/09-airdrops.md)) | Shorts a listed perpetual after Binance announces an airdrop of its token. +10% per trade over 32 events (t 1.7); +19.6% since 2025-10 (t 2.6, 14 events). About one event a month. Holds positions for 7 days. | **Not yet**: Demo Trading first |
| `exchange-delisting-short-7d` ([research 14](../research/14-exchanges.md)) | Shorts the Binance perpetual after Bybit or OKX announce delisting their own perpetual of the token. +4.4% per trade over 61 events with a 30% stop (t 1.8), positive in both years and in every variant tested. About 2.5 events a month. Holds positions for 7 days. | **Not yet**: Demo Trading first |
| `donchian24-*` ([research 02](../research/02-strategies.md)) | No reliable edge. | **No**, Demo Trading only |

**Simulated portfolio** ([research 08](../research/08-portfolio.md)). Over 2024-10 to 2026-09, at 3% risk per trade, the three event strategies (delisting, Monitoring Tag, busy listings) multiply equity by about 3.7 with a 14% realized drawdown. In 2026 alone they returned about +55% annualized.

These are in-sample upper bounds. Even so, they mean about +0.2% a day; the +10% per day target is not supported by any evidence found so far.

## Before the first live session

1. **Check the account:** `uv run mb check` must show no FAIL. A key without an IP restriction is allowed only with `ALLOW_UNRESTRICTED_IP=true` in `.env`, the owner's decision. Turn off margin loans on such a key; Binance deletes it after 30 days without orders.
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
uv run mb risk-scale 0.3         # first sessions: the smallest scale that still trades (see below)
uv run mb auto on                # let the strategies trade
uv run mb events --limit 30      # what happened
```

**Stopping:**

- `uv run mb session stop` closes everything and stops the bot.
- Closing terminal 1 has the same effect within about two minutes.

## The Telegram remote: every decision from the phone

The remote is a Telegram bot that runs on the bot's computer and answers only the owner's chat.

**Setup:**

1. In Telegram, ask @BotFather for `/newbot` and put the token into `.env` as `TELEGRAM_BOT_TOKEN`.
2. Run `uv run mb remote start` and send `/start` to your bot. It replies with your chat id.
3. Put the chat id into `.env` as `TELEGRAM_CHAT_ID`, then run `uv run mb remote stop` and `uv run mb remote start`.

**What it does:**

- **Notifications** from the live and demo bots: entries, closes, stop-outs, mode changes, errors, session loss, delisting announcements.
- **Your commands:** any allowed `mb` command, typed as `/mb ...`. `/help` lists examples. For example: `/mb session start`, `/mb strategies set delisting-short-4h`, `/mb risk-scale 0.3`, `/mb auto on`, `/mb restart`, `/mb close SOLUSDT`, `/mb transfer 50 to-futures`.
  - Only the `mb` program runs, never a shell.
  - Key generation, research, and data downloads stay on the computer.
- **Claude's proposals** (`mb ask`) arrive with Run and Decline buttons. They run only when you tap Run, and they expire.
- **A second tap** is required for closing positions, stopping a session, and transfers.
- **A daily summary** arrives at 09:00 computer time: both bots, equity against the window start, positions, the last 24 hours, and errors. Change the time with `SUMMARY_TIME` in `.env`, or ask for it any time with `/mb summary`.
- **On Mondays, an out-of-time check** follows the summary. For each strategy it shows the trades it would have made since 2026-10-01 against its research estimate (research 13). Ask for it any time with `/mb forward`.

**Free text** goes to Claude in the Claude app, not to the remote.

**Starting live from the phone.** Each step is an owner action.

1. `/mb check`: must pass.
2. `/mb transfer 100 to-futures`, then confirm. This funds the futures wallet.
3. `/mb strategies set delisting-short-4h`
4. `/mb session start`
5. `/mb risk-scale 0.3`: about a third of the normal risk while live fills are unproven.

   **Why not lower.** Binance rejects orders below 5 USDT of notional. At about 120 USDT of equity, a 20% stop, and 3% risk per trade:

   - at scale 0.25 the position would be 4.50 USDT, so every entry is rejected;
   - at scale 0.3 it is 5.45 USDT, risking 1.10 USDT.

   Strategies with a 40% stop (`listing-short-busy-7d`, `airdrop-short-7d`) need a scale of about 0.6 at this account size. At 0.6 the position is 5.46 USDT, risking 2.20 USDT; at 0.5 it would still be below the minimum.

   A rejected strategy entry is reported on the remote (`entry_rejected`).
6. `/mb auto on`: the strategies may trade from now on. On the very first start they are off. After that the setting is kept across restarts, so a restarted bot trades again by itself.

To stop: `/mb auto off` (no new trades) or `/mb session stop` (closes everything), then confirm.

The remote was tested on Demo Trading on 2026-10-22:

- proposals that were run, declined, and expired;
- a second confirmation for `close`;
- `strategies set` and `restart` from the phone.

**A session started from the remote** lives until `/mb session stop`. It is detached from the remote, so restarting the remote does not end it. On the computer, `uv run mb session start --detach` starts the same kind of session.

**What runs where.** Sessions, bots, and the remote run in the background without console windows. `mb ps` (or `/mb ps`) lists them with their process ids and start times. A process started in a terminal titles its window by its role, such as `mb live session`; processes started before 2026-10-22 may still show untitled `python` windows until their next restart. Do not close these windows:

- closing a bot's window stops the bot at once; its positions stay protected by their exchange-side stops, and the session ends;
- closing a session's window stops the heartbeat; about two minutes later the bot acts on its on-stop mode (by default it closes everything) and exits.

## Shutdowns, restarts, and power loss

**While the computer is off, the exchange still protects every position.**

- Each one has its exchange-side stop.
- The open-risk budget at entry limits the total loss if all stops fire.

**What is missing while it is off:**

- time exits, for example the 4-hour close of a delisting short;
- trailing stops and profit locking;
- notifications.

You can still close positions by hand in the Binance app; the bot notices that when it is back.

**Before a planned shutdown or restart:**

- `/mb session stop`, then confirm, to close everything;
- or `/mb auto off` to stop new entries and keep the open positions under their stops.

**After the computer is back:**

1. The remote starts by itself at logon, if you set up autostart (below).
2. The remote reports which bots run. If a session was on but its bot is not running (a restart or a power loss), the message has a **Start session** button.
3. The restarted bot reconciles with the exchange, adopts its positions, and immediately does any time exit that came due meanwhile.

**Autostart.** Press Win+R, type `shell:startup`, and put a shortcut to `scripts/start-remote.cmd` there. This is the owner's change to Windows.

**Knowing that the computer is down.**

1. Create a free check on healthchecks.io with a period of 1 minute and a grace of 5 minutes, and add Telegram as its integration.
2. Put the check's ping URL into `.env` as `HEALTHCHECK_URL`, then restart the remote.

The remote pings it every minute; when the pings stop, healthchecks.io alerts you.

**Windows settings** (the owner's):

- turn off sleep;
- set active hours so that updates do not restart the computer at night. Announcements arrive from 02:00 to 12:00 UTC.

## Keeping multi-day positions between sessions (optional)

`listing-short-busy-7d` holds a position for 7 days. The default session rule (a session ends, everything closes) would cut every trade short. To keep its positions open between sessions:

```powershell
$env:MB_STRATEGIES = "delisting-short-4h,monitoring-short-1d,listing-short-busy-7d"
$env:MB_ON_STOP = "keep"
uv run mb session start
```

**When the session ends,** the bot makes sure every position has its exchange-side stop and leaves the positions open.

**Between sessions, nothing manages them.**

- The stop is the only protection, and the window rules are not enforced.
- The open-risk budget at entry still limits the total loss if every stop fires.
- The 7-day time exit happens at the next session after the 7 days have passed.

## When to run sessions

The announcement strategies enter within 30 minutes of an announcement, so a session must be running when the announcement comes.

| Event type | Announcements since 2024-10 | When they came (UTC) | Days |
|---|---|---|---|
| Delisting | 39 | 37 between 02:00 and 12:00 | Mostly Monday to Friday, most often Wednesday |
| Monitoring Tag | 20 | All between 02:00 and 10:00 | Weekdays |

**For a UTC−3 time zone, that window is 23:00 to 09:00 local time:** a session running overnight on weekdays catches nearly all of them. Busy listings are entered the day after listing, until 12:00 UTC.

## What to expect

- **Most sessions will not trade.** Delisting announcements come about 1.5 times a month, busy listings a few times a week.
- **Position sizes are small.** At 3% risk per trade:
  - a delisting short (20% stop) is about 15% of equity;
  - a listing short (40% stop) is about 7.5% of equity.

## Known limits

- **Contracts being delisted.** Binance may block new positions in them; the bot then logs the rejection and skips the trade.
- **Announcement data.** It comes from Binance's public website endpoint, not an official API.
- **Thin markets.** Live fills in illiquid tokens can be worse than the simulation assumed.
