"""`mb`: the command-line interface to the bot."""

import asyncio
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Annotated

import httpx
import typer

from marketbot import control
from marketbot.checks.account import (
    CheckResult,
    Status,
    render_markdown,
    render_text,
    run_checks,
)
from marketbot.config import Settings
from marketbot.data import archive
from marketbot.exchange.client import Binance
from marketbot.exchange.signing import generate_ed25519_pem

app = typer.Typer(no_args_is_help=True, add_completion=False)
data_app = typer.Typer(no_args_is_help=True, help="Historical market data.")
app.add_typer(data_app, name="data")
research_app = typer.Typer(no_args_is_help=True, help="Research studies and reports.")
app.add_typer(research_app, name="research")
control.register(app)


@app.callback()
def main(
    ctx: typer.Context,
    env: Annotated[
        str, typer.Option(envvar="MB_ENV", help="live, or demo for Binance Demo Trading.")
    ] = "live",
) -> None:
    """Research trading bot for a small Binance account."""
    if env not in ("live", "demo"):
        raise typer.BadParameter("env must be live or demo")
    ctx.obj = env


PRIVATE_KEY_NAME = "binance_ed25519_private.pem"
PUBLIC_KEY_NAME = "binance_ed25519_public.pem"


@app.command()
def check(
    public_only: Annotated[
        bool, typer.Option("--public-only", help="Skip checks that need API credentials.")
    ] = False,
    markdown: Annotated[
        Path | None, typer.Option(help="Also write the report as markdown to this path.")
    ] = None,
) -> None:
    """Run read-only checks of connectivity, API key safety, and account setup."""
    settings = Settings()
    include_private = not public_only
    if include_private and not settings.has_credentials:
        typer.echo("No API credentials in .env; running public checks only.\n")
        include_private = False

    async def run() -> list[CheckResult]:
        async with Binance.from_settings(settings) as binance:
            return await run_checks(
                binance,
                include_private=include_private,
                allow_unrestricted_ip=settings.allow_unrestricted_ip,
            )

    try:
        results = asyncio.run(run())
    except (OSError, ValueError) as exc:
        typer.echo(f"Cannot load credentials: {exc}", err=True)
        raise typer.Exit(2) from exc

    typer.echo(render_text(results))
    if markdown is not None:
        markdown.parent.mkdir(parents=True, exist_ok=True)
        markdown.write_text(render_markdown(results, datetime.now(UTC)), encoding="utf-8")
        typer.echo(f"\nReport written to {markdown}")
    if any(result.status is Status.FAIL for result in results):
        raise typer.Exit(1)


@app.command()
def keygen(
    out_dir: Annotated[Path, typer.Option(help="Directory for the key files.")] = Path("secrets"),
    passphrase: Annotated[
        bool, typer.Option("--passphrase", help="Encrypt the private key (prompts for it).")
    ] = False,
) -> None:
    """Generate an Ed25519 key pair for a Binance API key."""
    private_path = out_dir / PRIVATE_KEY_NAME
    public_path = out_dir / PUBLIC_KEY_NAME
    existing = [path for path in (private_path, public_path) if path.exists()]
    if existing:
        typer.echo(f"Refusing to overwrite: {', '.join(map(str, existing))}", err=True)
        raise typer.Exit(1)

    secret = (
        typer.prompt("Passphrase", hide_input=True, confirmation_prompt=True)
        if passphrase
        else None
    )
    private_pem, public_pem = generate_ed25519_pem(secret)
    out_dir.mkdir(parents=True, exist_ok=True)
    private_path.write_bytes(private_pem)
    public_path.write_bytes(public_pem)

    typer.echo(f"Private key: {private_path} (keep it local; never commit or share it)")
    typer.echo(f"Public key:  {public_path}\n")
    typer.echo(public_pem.decode())
    typer.echo(
        "Register the public key on Binance (API Management > Create API > Self-generated),\n"
        "then put the API key and the private key path into .env."
    )


@data_app.command("download")
def data_download(
    dataset: Annotated[
        str, typer.Argument(help="klines or fundingRate.", metavar="DATASET")
    ] = "klines",
    interval: Annotated[str, typer.Option(help="Kline interval, e.g. 1d, 1h, 5m, 1m.")] = "1d",
    start: Annotated[str, typer.Option(help="First month, YYYY-MM.")] = "2024-10",
    end: Annotated[str, typer.Option(help="Last month, YYYY-MM.")] = "2026-09",
    symbols: Annotated[
        str | None, typer.Option(help="Comma-separated symbols; default: every USDT symbol.")
    ] = None,
    symbols_file: Annotated[
        Path | None, typer.Option(help="File with one symbol per line.")
    ] = None,
    jobs_file: Annotated[
        Path | None,
        typer.Option(help="File with 'SYMBOL YYYY-MM' per line; overrides symbols and months."),
    ] = None,
    root: Annotated[Path, typer.Option(help="Storage directory.")] = archive.DEFAULT_ROOT,
) -> None:
    """Download monthly USD-M futures data from data.binance.vision into Parquet files."""
    if dataset not in ("klines", "fundingRate"):
        raise typer.BadParameter("DATASET must be klines or fundingRate")
    months = list(archive.month_range(archive.Month.parse(start), archive.Month.parse(end)))

    async def run() -> archive.DownloadStats:
        async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
            if jobs_file:
                jobs = [
                    (symbol, archive.Month.parse(month))
                    for symbol, month in (line.split() for line in _lines(jobs_file))
                ]
            else:
                if symbols:
                    wanted = [symbol.strip().upper() for symbol in symbols.split(",") if symbol]
                elif symbols_file:
                    wanted = _lines(symbols_file)
                else:
                    wanted = await archive.list_symbols(client)
                jobs = [(symbol, month) for symbol in wanted for month in months]
            typer.echo(f"{len(jobs)} symbol-months of {dataset}")
            return await archive.download(
                client,
                dataset,  # type: ignore[arg-type]
                jobs,
                root=root,
                interval=interval if dataset == "klines" else None,
            )

    stats = asyncio.run(run())
    typer.echo(
        f"downloaded {stats.downloaded}, cached {stats.cached}, "
        f"not in archive {stats.missing}, failed {stats.failed}"
    )
    if stats.failed:
        raise typer.Exit(1)


@data_app.command("announcements")
def data_announcements(
    source: Annotated[str, typer.Option(help="binance, upbit, exchanges, or all.")] = "all",
    out: Annotated[Path, typer.Option(help="Directory to write.")] = Path("data/announcements"),
) -> None:
    """Download exchange announcements: Binance catalogs 48, 49, 161, Upbit's trade notices,
    and Bybit's and OKX's listings and delistings (`exchanges`)."""
    from marketbot.data import announcements, exchanges, upbit

    if source not in ("binance", "upbit", "exchanges", "all"):
        raise typer.BadParameter("--source must be binance, upbit, exchanges, or all")

    async def run() -> None:
        async with httpx.AsyncClient(timeout=30) as client:
            frames = {}
            if source in ("binance", "all"):
                for name, catalog in (
                    ("listings", announcements.NEW_LISTINGS),
                    ("news", announcements.NEWS),
                    ("delistings", announcements.DELISTINGS),
                ):
                    frames[name] = await announcements.fetch_catalog(client, catalog)
            if source in ("upbit", "all"):
                frames["upbit"] = await upbit.fetch_notices(client)
            if source in ("exchanges", "all"):
                frames["exchanges"] = await exchanges.fetch_all(client)
        for name, frame in frames.items():
            announcements.save(frame, out / f"{name}.parquet")
            typer.echo(f"{name}: {frame.height} announcements, {frame['released'].min()} on")

    asyncio.run(run())


@data_app.command("unlocks")
def data_unlocks(
    out: Annotated[Path, typer.Option(help="Directory to write.")] = Path("data/unlocks"),
) -> None:
    """Download token unlock schedules (DefiLlama datasets) and CoinGecko tickers."""
    import polars as pl

    from marketbot.data import unlocks

    async def run() -> tuple[pl.DataFrame, dict[str, str]]:
        async with httpx.AsyncClient(timeout=60) as client:
            documents = await unlocks.fetch_all(client)
            return unlocks.events_frame(documents), await unlocks.fetch_coingecko_symbols(client)

    events, symbols = asyncio.run(run())
    out.mkdir(parents=True, exist_ok=True)
    events.write_parquet(out / "cliffs.parquet")
    pl.DataFrame({"gecko_id": list(symbols), "ticker": list(symbols.values())}).write_parquet(
        out / "coingecko.parquet"
    )
    typer.echo(f"{events.height} cliff unlocks of {events['protocol'].n_unique()} protocols")


@research_app.command("universe")
def research_universe(
    start: Annotated[str, typer.Option(help="First day, YYYY-MM-DD.")] = "2025-10-01",
    end: Annotated[str, typer.Option(help="Last day, YYYY-MM-DD.")] = "2026-09-30",
    out: Annotated[Path, typer.Option(help="Jobs file to write.")] = Path(
        "data/research/universe-jobs.txt"
    ),
    root: Annotated[Path, typer.Option(help="Storage directory.")] = archive.DEFAULT_ROOT,
) -> None:
    """List the symbol-months of intraday data that the feasibility study needs."""
    from marketbot.research import feasibility

    universes = feasibility.study_universes(
        root, date.fromisoformat(start), date.fromisoformat(end)
    )
    jobs = feasibility.intraday_jobs(universes)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("".join(f"{symbol} {month}\n" for symbol, month in jobs), encoding="utf-8")
    typer.echo(
        f"{universes['symbol'].n_unique()} symbols, {len(jobs)} symbol-months written to {out}"
    )


@research_app.command("feasibility")
def research_feasibility(
    start: Annotated[str, typer.Option(help="First day, YYYY-MM-DD.")] = "2025-10-01",
    end: Annotated[str, typer.Option(help="Last day, YYYY-MM-DD.")] = "2026-09-30",
    interval: Annotated[str, typer.Option(help="Intraday kline interval.")] = "5m",
    out: Annotated[Path, typer.Option(help="Report to write.")] = Path(
        "research/results/01-feasibility.md"
    ),
    root: Annotated[Path, typer.Option(help="Storage directory.")] = archive.DEFAULT_ROOT,
) -> None:
    """Run research 01 (is +10% per window reachable?) and write the generated report."""
    from marketbot.research import feasibility

    report = feasibility.run_study(
        root, date.fromisoformat(start), date.fromisoformat(end), interval, progress=typer.echo
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(report, encoding="utf-8")
    typer.echo(f"Report written to {out}")


def _lines(path: Path) -> list[str]:
    return [line.strip() for line in path.read_text().splitlines() if line.strip()]


@research_app.command("strategies")
def research_strategies(
    start: Annotated[str, typer.Option(help="First day, YYYY-MM-DD.")] = "2025-10-01",
    split: Annotated[str, typer.Option(help="First out-of-sample day.")] = "2026-04-01",
    end: Annotated[str, typer.Option(help="Last day, YYYY-MM-DD.")] = "2026-09-30",
    out: Annotated[Path, typer.Option(help="Report to write.")] = Path(
        "research/results/02-strategies.md"
    ),
    trades_out: Annotated[Path, typer.Option(help="All simulated trades (Parquet).")] = Path(
        "data/research/02-trades.parquet"
    ),
    root: Annotated[Path, typer.Option(help="Storage directory.")] = archive.DEFAULT_ROOT,
) -> None:
    """Run research 02 (intraday strategies, in-sample and out-of-sample)."""
    from marketbot.research import strategies

    period = strategies.StudyPeriod(
        date.fromisoformat(start), date.fromisoformat(split), date.fromisoformat(end)
    )
    from marketbot.research.feasibility import non_crypto_symbols

    info = httpx.get("https://fapi.binance.com/fapi/v1/exchangeInfo", timeout=30).json()
    trades = strategies.run_trades(
        root, period, exclude=non_crypto_symbols(info), progress=typer.echo
    )
    trades_out.parent.mkdir(parents=True, exist_ok=True)
    trades.write_parquet(trades_out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(strategies.report(trades, period), encoding="utf-8")
    typer.echo(f"{trades.height} trades; report written to {out}")


@research_app.command("listings")
def research_listings(
    archive_start: Annotated[str, typer.Option(help="First day of the archive window.")] = (
        "2024-10-01"
    ),
    split: Annotated[str, typer.Option(help="Split listings by this date.")] = "2025-10-01",
    out: Annotated[Path, typer.Option(help="Report to write.")] = Path(
        "research/results/03-listings.md"
    ),
    root: Annotated[Path, typer.Option(help="Storage directory.")] = archive.DEFAULT_ROOT,
) -> None:
    """Run research 03 (short newly listed perpetuals)."""
    from marketbot.research import listings
    from marketbot.research.feasibility import non_crypto_symbols

    info = httpx.get("https://fapi.binance.com/fapi/v1/exchangeInfo", timeout=30).json()
    trades, text = listings.run(
        root,
        date.fromisoformat(archive_start),
        date.fromisoformat(split),
        exclude=non_crypto_symbols(info),
    )
    trades.write_parquet(Path("data/research/03-listing-trades.parquet"))
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    typer.echo(f"{trades.height} trades; report written to {out}")


@research_app.command("delistings")
def research_delistings(
    since: Annotated[str, typer.Option(help="First announcement day.")] = "2024-10-01",
    out: Annotated[Path, typer.Option(help="Report to write.")] = Path(
        "research/results/04-delistings.md"
    ),
    root: Annotated[Path, typer.Option(help="Storage directory.")] = archive.DEFAULT_ROOT,
) -> None:
    """Run research 04 (perpetuals after Binance delisting announcements)."""
    from marketbot.research import delistings

    start = datetime.fromisoformat(since).replace(tzinfo=UTC)
    frame, text = delistings.run(root, Path("data/announcements/delistings.parquet"), start)
    frame.write_parquet(Path("data/research/04-delisting-events.parquet"))
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    typer.echo(f"{frame.height} events; report written to {out}")


@research_app.command("spot-listings")
def research_spot_listings(
    since: Annotated[str, typer.Option(help="First announcement day.")] = "2024-10-01",
    out: Annotated[Path, typer.Option(help="Report to write.")] = Path(
        "research/results/05-spot-listings.md"
    ),
    root: Annotated[Path, typer.Option(help="Storage directory.")] = archive.DEFAULT_ROOT,
) -> None:
    """Run research 05 (long perpetuals after Binance spot listing announcements)."""
    from marketbot.data.announcements import spot_listed_perpetuals
    from marketbot.research import delistings

    start = datetime.fromisoformat(since).replace(tzinfo=UTC)
    frame, text = delistings.run(
        root,
        Path("data/announcements/listings.parquet"),
        start,
        parser=spot_listed_perpetuals,
        direction=1,
        heading="# Research 05 results: spot listing announcements",
        command="uv run mb research spot-listings",
        analysis="../05-spot-listings.md",
        kind="spot listing",
    )
    frame.write_parquet(Path("data/research/05-spot-listing-events.parquet"))
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    typer.echo(f"{frame.height} events; report written to {out}")


@app.command()
def market() -> None:
    """Print a read-only market brief from public data (trend, sentiment, funding, events)."""
    from marketbot.market import brief

    typer.echo(asyncio.run(brief()))


@research_app.command("funding")
def research_funding(
    split: Annotated[str, typer.Option(help="First day of the second year.")] = "2025-10-01",
    out: Annotated[Path, typer.Option(help="Report to write.")] = Path(
        "research/results/06-funding.md"
    ),
    root: Annotated[Path, typer.Option(help="Storage directory.")] = archive.DEFAULT_ROOT,
) -> None:
    """Run research 06 (extreme funding rates)."""
    from marketbot.research import funding
    from marketbot.research.feasibility import non_crypto_symbols

    info = httpx.get("https://fapi.binance.com/fapi/v1/exchangeInfo", timeout=30).json()
    trades, text = funding.run(root, non_crypto_symbols(info), date.fromisoformat(split))
    trades.write_parquet(Path("data/research/06-funding-trades.parquet"))
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    typer.echo(f"{trades.height} trades; report written to {out}")


@research_app.command("monitoring")
def research_monitoring(
    since: Annotated[str, typer.Option(help="First announcement day.")] = "2024-10-01",
    out: Annotated[Path, typer.Option(help="Report to write.")] = Path(
        "research/results/07-monitoring-tag.md"
    ),
    root: Annotated[Path, typer.Option(help="Storage directory.")] = archive.DEFAULT_ROOT,
) -> None:
    """Run research 07 (short perpetuals after Binance adds the Monitoring Tag)."""
    from marketbot.data.announcements import monitoring_tagged_perpetuals
    from marketbot.research import delistings

    start = datetime.fromisoformat(since).replace(tzinfo=UTC)
    frame, text = delistings.run(
        root,
        Path("data/announcements/news.parquet"),
        start,
        parser=monitoring_tagged_perpetuals,
        direction=-1,
        heading="# Research 07 results: Monitoring Tag announcements",
        command="uv run mb research monitoring",
        analysis="../07-monitoring-tag.md",
        kind="Monitoring Tag",
    )
    frame.write_parquet(Path("data/research/07-monitoring-events.parquet"))
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    typer.echo(f"{frame.height} events; report written to {out}")


@research_app.command("portfolio")
def research_portfolio(
    out: Annotated[Path, typer.Option(help="Report to write.")] = Path(
        "research/results/08-portfolio.md"
    ),
) -> None:
    """Run research 08 (event strategies as a portfolio under the risk rules)."""
    from marketbot.research import portfolio

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(portfolio.run(), encoding="utf-8")
    typer.echo(f"Report written to {out}")


@research_app.command("airdrops")
def research_airdrops(
    since: Annotated[str, typer.Option(help="First announcement day.")] = "2024-10-01",
    split: Annotated[str, typer.Option(help="First day of the second year.")] = "2025-10-01",
    out: Annotated[Path, typer.Option(help="Report to write.")] = Path(
        "research/results/09-airdrops.md"
    ),
    root: Annotated[Path, typer.Option(help="Storage directory.")] = archive.DEFAULT_ROOT,
) -> None:
    """Run research 09 (listing types, and shorts after Binance airdrop announcements).

    Needs the research 03 trades and the announcements catalogs 48 and 49.
    """
    import polars as pl

    from marketbot.research import airdrops

    titles = pl.concat(
        pl.read_parquet(Path("data/announcements") / name).select("released", "title")
        for name in ("listings.parquet", "news.parquet")
    ).unique()
    kinds, events, text = airdrops.run(
        root,
        pl.read_parquet(Path("data/research/03-listing-trades.parquet")),
        titles,
        date.fromisoformat(split),
        datetime.fromisoformat(since).replace(tzinfo=UTC),
    )
    kinds.write_parquet(Path("data/research/09-listing-kinds.parquet"))
    events.write_parquet(Path("data/research/09-airdrop-shorts.parquet"))
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    typer.echo(f"{kinds.height} listings, {events.height} event shorts; report written to {out}")


@research_app.command("upbit")
def research_upbit(
    since: Annotated[str, typer.Option(help="First announcement day.")] = "2024-10-01",
    split: Annotated[str, typer.Option(help="First day of the second year.")] = "2025-10-01",
    jobs: Annotated[
        Path | None,
        typer.Option(help="Only write the 5-minute kline months the study needs to this file."),
    ] = None,
    out: Annotated[Path, typer.Option(help="Report to write.")] = Path(
        "research/results/10-upbit.md"
    ),
    root: Annotated[Path, typer.Option(help="Storage directory.")] = archive.DEFAULT_ROOT,
) -> None:
    """Run research 10 (Binance perpetuals after Upbit announcements).

    Needs `mb data announcements --source upbit`, then the 5-minute klines listed by --jobs.
    """
    import polars as pl

    from marketbot.research import upbit

    notices = pl.read_parquet(Path("data/announcements/upbit.parquet"))
    symbols = set(archive.load("klines", root=root, interval="1d")["symbol"].unique())
    start = datetime.fromisoformat(since).replace(tzinfo=UTC)
    if jobs is not None:
        needed = upbit.jobs(upbit.event_frame(notices, start, symbols))
        jobs.write_text("".join(f"{symbol} {month}\n" for symbol, month in needed))
        typer.echo(f"{len(needed)} symbol-months written to {jobs}")
        return
    frame, shorts, text = upbit.run(root, notices, start, date.fromisoformat(split), symbols)
    frame.write_parquet(Path("data/research/10-upbit-events.parquet"))
    shorts.write_parquet(Path("data/research/10-upbit-shorts.parquet"))
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    typer.echo(f"{frame.height} events; report written to {out}")


@research_app.command("forward")
def research_forward(
    since: Annotated[str, typer.Option(help="First event day.")] = "2026-10-01",
    out: Annotated[Path, typer.Option(help="Report to write.")] = Path(
        "research/results/13-out-of-time.md"
    ),
) -> None:
    """Run research 13 (the event strategies on events after the research data ends)."""
    from marketbot.research import forward

    start = datetime.fromisoformat(since).replace(tzinfo=UTC)
    now = datetime.now(UTC)
    frame = asyncio.run(forward.collect(start, now))
    frame.write_parquet(Path("data/research/13-forward.parquet"))
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(forward.report(frame, start, now), encoding="utf-8")
    typer.echo(f"{frame.height} events; report written to {out}")


@research_app.command("reversal")
def research_reversal(
    split: Annotated[str, typer.Option(help="First day of the second year.")] = "2025-10-01",
    out: Annotated[Path, typer.Option(help="Report to write.")] = Path(
        "research/results/12-reversal.md"
    ),
    root: Annotated[Path, typer.Option(help="Storage directory.")] = archive.DEFAULT_ROOT,
) -> None:
    """Run research 12 (cross-sectional weekly reversal and momentum)."""
    from marketbot.research import reversal
    from marketbot.research.feasibility import non_crypto_symbols

    info = httpx.get("https://fapi.binance.com/fapi/v1/exchangeInfo", timeout=30).json()
    weeks, text = reversal.run(root, non_crypto_symbols(info), date.fromisoformat(split))
    weeks.write_parquet(Path("data/research/12-reversal-weeks.parquet"))
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    typer.echo(f"{weeks.height} weekly cohorts; report written to {out}")


@research_app.command("exchanges")
def research_exchanges(
    since: Annotated[str, typer.Option(help="First announcement day.")] = "2024-10-01",
    split: Annotated[str, typer.Option(help="First day of the second year.")] = "2025-10-01",
    jobs: Annotated[
        Path | None,
        typer.Option(help="Only write the kline and funding months the study needs to this file."),
    ] = None,
    out: Annotated[Path, typer.Option(help="Report to write.")] = Path(
        "research/results/14-exchanges.md"
    ),
    root: Annotated[Path, typer.Option(help="Storage directory.")] = archive.DEFAULT_ROOT,
) -> None:
    """Run research 14 (Binance perpetuals after Bybit and OKX announcements).

    Needs `mb data announcements --source exchanges`, then the data listed by --jobs.
    """
    from datetime import timedelta

    import polars as pl

    from marketbot.research import exchanges
    from marketbot.research.feasibility import non_crypto_symbols

    notices = pl.read_parquet(Path("data/announcements/exchanges.parquet"))
    binance = pl.read_parquet(Path("data/announcements/delistings.parquet"))
    info = httpx.get("https://fapi.binance.com/fapi/v1/exchangeInfo", timeout=30).json()
    symbols = set(archive.load("klines", root=root, interval="1d")["symbol"].unique())
    symbols -= non_crypto_symbols(info)
    start = datetime.fromisoformat(since).replace(tzinfo=UTC)
    if jobs is not None:
        events = exchanges.event_frame(notices, binance, start, symbols)
        needed = sorted(
            {
                (symbol, f"{released + timedelta(days=offset):%Y-%m}")
                for symbol, released in events.select("symbol", "released").iter_rows()
                for offset in (-2, 0, 8, 15)
            }
            | {("BTCUSDT", f"{day:%Y-%m}") for day in events["released"]}
        )
        jobs.write_text("".join(f"{symbol} {month}" + chr(10) for symbol, month in needed))
        typer.echo(f"{len(needed)} symbol-months written to {jobs}")
        return
    frame, shorts, text = exchanges.run(
        root, notices, binance, start, date.fromisoformat(split), symbols
    )
    frame.write_parquet(Path("data/research/14-exchange-events.parquet"))
    shorts.write_parquet(Path("data/research/14-exchange-shorts.parquet"))
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    typer.echo(f"{frame.height} events; report written to {out}")


@research_app.command("distress")
def research_distress(
    since: Annotated[str, typer.Option(help="First tag day.")] = "2024-10-01",
    split: Annotated[str, typer.Option(help="First day of the second year.")] = "2025-10-01",
    out: Annotated[Path, typer.Option(help="Report to write.")] = Path(
        "research/results/15-distress.md"
    ),
    root: Annotated[Path, typer.Option(help="Storage directory.")] = archive.DEFAULT_ROOT,
) -> None:
    """Run research 15 (perpetuals under the Monitoring Tag, over weeks)."""
    import polars as pl

    from marketbot.research import distress

    trades, text = distress.run(
        root,
        pl.read_parquet(Path("data/announcements/news.parquet")),
        pl.read_parquet(Path("data/announcements/delistings.parquet")),
        datetime.fromisoformat(since).replace(tzinfo=UTC),
        date.fromisoformat(split),
    )
    trades.write_parquet(Path("data/research/15-distress-trades.parquet"))
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    typer.echo(f"{trades.height} trades; report written to {out}")


@research_app.command("unlocks")
def research_unlocks(
    start: Annotated[str, typer.Option(help="First unlock day.")] = "2024-10-15",
    end: Annotated[str, typer.Option(help="Day after the last unlock day.")] = "2026-09-20",
    split: Annotated[str, typer.Option(help="First day of the second year.")] = "2025-10-01",
    jobs: Annotated[
        Path | None,
        typer.Option(help="Only write the funding-rate months the study needs to this file."),
    ] = None,
    out: Annotated[Path, typer.Option(help="Report to write.")] = Path(
        "research/results/11-unlocks.md"
    ),
    root: Annotated[Path, typer.Option(help="Storage directory.")] = archive.DEFAULT_ROOT,
) -> None:
    """Run research 11 (perpetuals around scheduled token unlocks).

    Needs `mb data unlocks`, then the funding rates listed by --jobs.
    """
    from datetime import timedelta

    import polars as pl

    from marketbot.research import unlocks

    cliffs = pl.read_parquet(Path("data/unlocks/cliffs.parquet"))
    tickers = dict(pl.read_parquet(Path("data/unlocks/coingecko.parquet")).iter_rows())
    symbols = set(archive.load("klines", root=root, interval="1d")["symbol"].unique())
    first, last = date.fromisoformat(start), date.fromisoformat(end)
    if jobs is not None:
        events = unlocks.unlock_events(cliffs, tickers, symbols, first, last)
        needed = sorted(
            {
                (symbol, f"{day + timedelta(days=offset):%Y-%m}")
                for symbol, day in events.select("symbol", "day").iter_rows()
                for offset in (-15, 0, 8)
            }
        )
        jobs.write_text("".join(f"{symbol} {month}\n" for symbol, month in needed))
        typer.echo(f"{len(needed)} symbol-months written to {jobs}")
        return
    trades, text = unlocks.run(
        root, cliffs, tickers, symbols, first, last, date.fromisoformat(split)
    )
    trades.write_parquet(Path("data/research/11-unlock-trades.parquet"))
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    typer.echo(f"{trades.height} events in the main variant; report written to {out}")
