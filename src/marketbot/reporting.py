"""`mb report`: what the bot did over a period, from its journal and Binance's income history.

The income history (`/fapi/v1/income`, read-only) is the exchange's own accounting of realized
PnL, commissions, and funding per symbol, so the money figures do not depend on the bot's
bookkeeping.
"""

from collections import Counter
from datetime import datetime
from typing import Any

import polars as pl

from marketbot.exchange.rest import BinanceREST
from marketbot.state.store import Store

MONEY_TYPES = ("REALIZED_PNL", "COMMISSION", "FUNDING_FEE")


async def fetch_income(rest: BinanceREST, start: datetime, end: datetime) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    start_ms, end_ms = int(start.timestamp() * 1000), int(end.timestamp() * 1000)
    while True:
        page = await rest.signed(
            "GET", "/fapi/v1/income", {"startTime": start_ms, "endTime": end_ms, "limit": 1000}
        )
        rows += page
        if len(page) < 1000:
            return rows
        start_ms = int(page[-1]["time"]) + 1


def summarize_income(rows: list[dict[str, Any]]) -> pl.DataFrame:
    """Per symbol: realized PnL, commission, funding, and their net, in the income asset."""
    money = [row for row in rows if row["incomeType"] in MONEY_TYPES]
    if not money:
        return pl.DataFrame(
            schema={c: pl.Float64 for c in ("realized", "commission", "funding", "net")}
            | {"symbol": pl.String}
        )
    frame = pl.DataFrame(
        {
            "symbol": [row["symbol"] or "-" for row in money],
            "type": [row["incomeType"] for row in money],
            "income": [float(row["income"]) for row in money],
        }
    )
    pivot = frame.pivot(on="type", index="symbol", values="income", aggregate_function="sum")
    for column in MONEY_TYPES:
        if column not in pivot.columns:
            pivot = pivot.with_columns(pl.lit(0.0).alias(column))
    return (
        pivot.fill_null(0.0)
        .select(
            "symbol",
            realized=pl.col("REALIZED_PNL"),
            commission=pl.col("COMMISSION"),
            funding=pl.col("FUNDING_FEE"),
        )
        .with_columns(net=pl.col("realized") + pl.col("commission") + pl.col("funding"))
        .sort("net")
    )


def journal_summary(store: Store, start: datetime) -> dict[str, Any]:
    events = [event for event in store.events(limit=100_000) if event.ts >= start]
    entries = [e.payload for e in events if e.kind == "entry_filled"]
    closes = Counter(e.payload.get("reason", "?") for e in events if e.kind == "position_closed")
    closes["stop or target on the exchange"] += sum(e.kind == "position_gone" for e in events)
    return {
        "events": len(events),
        "entries": entries,
        "entries_by_strategy": Counter(entry.get("strategy", "?") for entry in entries),
        "closes": closes,
        "mode_changes": [e.payload for e in events if e.kind == "mode_changed"],
        "errors": Counter(e.kind for e in events if e.kind.endswith(("_error", "_failed"))),
    }


def income_table(income: pl.DataFrame) -> list[str]:
    """Aligned columns, narrow enough for a phone screen (about 34 characters)."""
    rows = income.sort("net", descending=True).iter_rows(named=True)
    lines = [f"{'symbol':<11}{'net':>8}{'pnl':>8}{'fees':>7}"]
    for row in rows:
        symbol = row["symbol"].removesuffix("USDT")[:10]
        lines.append(
            f"{symbol:<11}{row['net']:>+8.2f}{row['realized']:>+8.2f}{row['commission']:>+7.2f}"
        )
    return lines


def render(start: datetime, end: datetime, income: pl.DataFrame, journal: dict[str, Any]) -> str:
    lines = [f"Report {start:%m-%d %H:%M} to {end:%m-%d %H:%M} UTC", ""]
    if income.height:
        totals = income.select(pl.exclude("symbol").sum()).row(0, named=True)
        lines += [
            f"Net {totals['net']:+.2f} USDT",
            f"  pnl {totals['realized']:+.2f}, fees {totals['commission']:+.2f},"
            f" funding {totals['funding']:+.2f}",
            "",
            *income_table(income),
            "",
        ]
    else:
        lines += ["No exchange income in this period.", ""]
    lines.append(f"Entries: {len(journal['entries'])}")
    for strategy, count in journal["entries_by_strategy"].most_common():
        lines.append(f"  {count} x {strategy}")
    closes = [(reason, count) for reason, count in journal["closes"].most_common() if count]
    if closes:
        lines.append("Closes:")
        lines += [f"  {count} x {reason}" for reason, count in closes]
    for change in journal["mode_changes"]:
        lines.append(f"Mode {change.get('previous')} -> {change.get('current')}")
        lines.append(f"  {change.get('reason')}")
    if journal["errors"]:
        lines.append("Errors: " + ", ".join(f"{k} x{v}" for k, v in journal["errors"].items()))
    return "\n".join(lines)


def summary_block(
    env: str,
    running: bool,
    snapshot: dict[str, Any] | None,
    income: pl.DataFrame | None,
    journal: dict[str, Any],
) -> list[str]:
    """A few lines per bot for the daily Telegram summary."""
    head = f"{env.upper()}: bot {'running' if running else 'NOT running'}"
    window = (snapshot or {}).get("window") or {}
    account = (snapshot or {}).get("account") or {}
    if window:
        head += f", mode {window.get('mode')}"
    lines = [head]
    if account and window:
        equity, e0 = float(account["equity"]), float(window["e0"])
        lines.append(f"  equity {equity:.2f} (window start {e0:.2f}, {(equity / e0 - 1):+.1%})")
        positions = account.get("positions") or []
        held = ", ".join(f"{p['symbol']} {p['side']}" for p in positions) or "none"
        lines.append(f"  positions: {held}")
    closes = sum(journal["closes"].values())
    net = "n/a"
    if income is not None:
        totals = (
            income.select(pl.exclude("symbol").sum()).row(0, named=True) if income.height else {}
        )
        net = f"{totals.get('net', 0.0):+.2f} USDT"
    lines.append(f"  last 24h: {len(journal['entries'])} entries, {closes} closes, net {net}")
    for change in journal["mode_changes"]:
        lines.append(f"  mode {change.get('previous')} -> {change.get('current')}")
    errors = {k: v for k, v in journal["errors"].items()}
    if errors:
        lines.append("  errors: " + ", ".join(f"{k} x{v}" for k, v in errors.items()))
    return lines
