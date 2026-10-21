"""`mb`: the command-line interface to the bot."""

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

import typer

from marketbot.checks.account import (
    CheckResult,
    Status,
    render_markdown,
    render_text,
    run_checks,
)
from marketbot.config import Settings
from marketbot.exchange.client import Binance
from marketbot.exchange.signing import generate_ed25519_pem

app = typer.Typer(no_args_is_help=True, add_completion=False)

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
