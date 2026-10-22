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
from marketbot.research.feasibility import markdown_table
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


def render(start: datetime, end: datetime, income: pl.DataFrame, journal: dict[str, Any]) -> str:
    lines = [f"Report {start:%Y-%m-%d %H:%M} to {end:%Y-%m-%d %H:%M} UTC", ""]
    if income.height:
        totals = income.select(pl.exclude("symbol").sum()).row(0, named=True)
        lines.append(
            f"Exchange income (USDT): net {totals['net']:+.4f} = realized {totals['realized']:+.4f}"
            f" + commission {totals['commission']:+.4f} + funding {totals['funding']:+.4f}"
        )
        lines += ["", markdown_table(income, 4), ""]
    else:
        lines += ["Exchange income: none in this period.", ""]
    lines.append(f"Journal: {journal['events']} events, {len(journal['entries'])} entries")
    for strategy, count in journal["entries_by_strategy"].most_common():
        lines.append(f"  entries by {strategy}: {count}")
    for reason, count in journal["closes"].most_common():
        if count:
            lines.append(f"  closes ({reason}): {count}")
    for change in journal["mode_changes"]:
        lines.append(
            f"  mode {change.get('previous')} -> {change.get('current')}: {change.get('reason')}"
        )
    if journal["errors"]:
        lines.append("  errors: " + ", ".join(f"{k} x{v}" for k, v in journal["errors"].items()))
    return "\n".join(lines)
