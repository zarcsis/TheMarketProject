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
            return await run_checks(binance, include_private=include_private)

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
