"""Drill the event strategies end to end on Binance Demo Trading.

A synthetic delisting announcement and a synthetic listing drive the live strategy code;
everything else is real: the engine, the risk manager, and orders on Demo Trading. The
hold periods are shortened to minutes so the time exits happen during the drill.

Usage: uv run python scripts/drill_event_strategies.py  (no demo bot may be running)
"""

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from tempfile import mkdtemp

import polars as pl

from marketbot.config import FUTURES_BASE_URL, Settings
from marketbot.engine import Engine
from marketbot.exchange.client import Binance
from marketbot.exchange.futures import BinanceFutures
from marketbot.exchange.rest import BinanceREST
from marketbot.exchange.virtual import VirtualBalance
from marketbot.risk.config import load_risk_config
from marketbot.state.store import Store
from marketbot.strategy.delisting import DelistingShort, DelistingShortConfig
from marketbot.strategy.listing import ListingShort, ListingShortConfig
from marketbot.strategy.runtime import Klines

DELISTED = "DOGEUSDT"  # stands in for an announced token: any liquid perpetual works
LISTED = "XRPUSDT"  # stands in for yesterday's listing


class DrillKlines(Klines):
    """Real klines, plus a synthetic listing of LISTED yesterday with a busy first day."""

    async def exchange_info(self, max_age=timedelta(minutes=10)) -> dict:
        info = await super().exchange_info(max_age)
        yesterday = datetime.now(UTC) - timedelta(days=1)
        symbols = [
            {**row, "onboardDate": int(yesterday.timestamp() * 1000)}
            if row["symbol"] == LISTED
            else row
            for row in info["symbols"]
        ]
        return {**info, "symbols": symbols}

    async def get(self, symbol: str, interval: str, limit: int) -> pl.DataFrame:
        frame = await super().get(symbol, interval, limit)
        if symbol == LISTED and interval == "1d":
            yesterday = (datetime.now(UTC) - timedelta(days=1)).date()
            frame = frame.with_columns(
                quote_volume=pl.when(pl.col("open_time").dt.date() == yesterday)
                .then(9e7)
                .otherwise(pl.col("quote_volume"))
            )
        return frame


async def main() -> None:
    settings = Settings().for_env("demo")
    store = Store(Path(mkdtemp()) / "drill.db")
    announced = datetime.now(UTC) - timedelta(minutes=6)

    async def fetch() -> pl.DataFrame:
        title = f"Binance Will Delist {DELISTED.removesuffix('USDT')} on 2026-11-30"
        return pl.DataFrame({"title": [title], "released": [announced]})

    async with Binance.from_settings(settings) as binance:
        await binance.futures.sync_time()
        venue = BinanceFutures(binance.futures)
        actual = (await venue.account()).equity
        offset = actual - Decimal(125)
        market = BinanceREST(FUTURES_BASE_URL, "/fapi/v1/time")
        klines = DrillKlines(market)
        delisting = DelistingShort(
            klines, DelistingShortConfig(hold=timedelta(minutes=3)), fetch=fetch, journal=store.log
        )
        listing = ListingShort(klines, ListingShortConfig(), journal=store.log)
        listing.config = replace(listing.config, last_entry_hour=24)
        engine = Engine(
            VirtualBalance(venue, lambda: offset),
            store,
            load_risk_config(),
            strategies=[delisting, listing],
        )
        store.enqueue("auto", {"on": True})
        deadline = datetime.now(UTC) + timedelta(minutes=6)
        while datetime.now(UTC) < deadline:
            await engine.tick()
            positions = {
                s: (p.side.value, str(p.qty), str(p.stop)) for s, p in engine.managed().items()
            }
            print(f"{datetime.now(UTC):%H:%M:%S} managed {positions}")
            if (
                engine.managed()
                and any(p.symbol == LISTED for p in engine.managed().values())
                and DELISTED not in engine.managed()
                and delisting._done
            ):
                break
            await asyncio.sleep(20)
        # The listing short holds for days; close it at the end of the drill.
        await engine.executor.flatten("end of drill")
        await market.close()
    for event in store.events(limit=500):
        if event.kind in (
            "delisting_announced",
            "strategy_entry",
            "entry_filled",
            "position_closed",
            "stop_failed",
            "strategy_error",
            "listing_skipped",
        ):
            print(f"{event.ts:%H:%M:%S} {event.kind} {str(event.payload)[:160]}")


if __name__ == "__main__":
    asyncio.run(main())
