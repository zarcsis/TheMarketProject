"""Session and trading commands of `mb`: the control plane between Claude and the bot.

Every command that changes trading goes to the running bot through its command queue, so
it passes the same risk checks as the bot's own decisions. The only exception is
`mb flatten` when no bot is running: it closes everything directly.
"""

import asyncio
import os
import sys
import time
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Annotated, Any

import psutil
import typer

from marketbot.config import FUTURES_BASE_URL, Env, Settings, log_dir, state_dir
from marketbot.engine import Engine, Heartbeat
from marketbot.exchange.client import Binance
from marketbot.exchange.futures import BinanceFutures, Exchange
from marketbot.exchange.rest import BinanceREST
from marketbot.exchange.virtual import VirtualBalance
from marketbot.execution.executor import Executor
from marketbot.risk.config import DEFAULT_PATH as DEFAULT_RISK_PATH
from marketbot.risk.config import RiskConfig, load_risk_config
from marketbot.session import bot_running, keep_alive, spawn_bot
from marketbot.state.store import Store
from marketbot.strategy import presets
from marketbot.strategy.runtime import Klines

session_app = typer.Typer(no_args_is_help=True, help="Start and stop trading sessions.")
strategies_app = typer.Typer(no_args_is_help=True, help="Choose the strategies the bot runs.")

TIME_SYNC_INTERVAL_S = 1800


def register(app: typer.Typer) -> None:
    app.add_typer(session_app, name="session")
    app.add_typer(strategies_app, name="strategies")
    app.add_typer(remote_app, name="remote")
    commands = (
        bot,
        status,
        events,
        report,
        pause,
        resume,
        risk_scale,
        auto,
        propose,
        close,
        flatten,
        restart,
        on_stop,
        transfer,
        ask,
        demo_shift,
    )
    for command in commands:
        app.command(hidden=command in (bot, demo_shift))(command)


class Paths:
    def __init__(self, env: Env) -> None:
        self.env = env
        self.store = state_dir(env) / "bot.db"
        self.heartbeat = state_dir(env) / "heartbeat"
        self.pid = state_dir(env) / "bot.pid"
        self.log = log_dir(env) / "bot.log"


def _env(ctx: typer.Context) -> Env:
    return ctx.obj or "live"


# The bot process.


def bot(ctx: typer.Context) -> None:
    """Run the trading engine in the foreground (started by `mb session start`)."""
    env = _env(ctx)
    paths = Paths(env)
    store = Store(paths.store)
    risk_path = Path(os.environ.get("MB_RISK_CONFIG") or DEFAULT_RISK_PATH)
    if env == "live" and risk_path.resolve() != DEFAULT_RISK_PATH.resolve():
        reason = f"refused: live trading only with {DEFAULT_RISK_PATH}"
        store.log("engine_refused", reason=reason)
    else:
        config = load_risk_config(risk_path)
        store.log("risk_config", path=str(risk_path), config=config.model_dump())
        reason = asyncio.run(_run_bot(env, paths, store, config))
    typer.echo(f"{datetime.now(UTC).isoformat()} bot stopped: {reason}")


async def _run_bot(env: Env, paths: Paths, store: Store, config: RiskConfig) -> str:
    settings = Settings().for_env(env)
    heartbeat = Heartbeat(paths.heartbeat)
    if not heartbeat.alive(datetime.now(UTC)):
        store.log("engine_refused", reason="no fresh session heartbeat")
        return "no fresh session heartbeat"
    async with Binance.from_settings(settings) as binance:
        await binance.futures.sync_time()
        venue = await _venue(env, settings, binance, store)
        # Signals always use the live venue's public klines, also when trading on Demo.
        market = BinanceREST(FUTURES_BASE_URL, "/fapi/v1/time")
        names = os.environ.get("MB_STRATEGIES") or store.get("strategies") or ""
        strategies = presets.build(names, Klines(market), store.log, store=store)
        store.log("strategies", names=[s.name for s in strategies])
        keep = (os.environ.get("MB_ON_STOP") or store.get("on_stop") or "flatten") == "keep"
        store.log("on_stop", mode="keep positions" if keep else "flatten")
        engine = Engine(
            venue,
            store,
            config,
            heartbeat=heartbeat,
            strategies=strategies,
            keep_positions_on_stop=keep,
        )
        sync = asyncio.create_task(_keep_time_synced(binance))
        try:
            return await engine.run()
        finally:
            sync.cancel()
            await market.close()


async def _venue(env: Env, settings: Settings, binance: Binance, store: Store) -> Exchange:
    if env == "live":
        return BinanceFutures(binance.futures, binance.spot)
    venue = BinanceFutures(binance.futures)
    offset = store.get("demo_offset")
    if offset is None:
        actual = (await venue.account()).equity
        offset = actual - Decimal(str(settings.demo_equity))
        store.put("demo_offset", offset)
        store.log("demo_offset", actual_equity=actual, offset=offset)
    return VirtualBalance(venue, lambda: Decimal(store.get("demo_offset")))


async def _keep_time_synced(binance: Binance) -> None:
    while True:
        await asyncio.sleep(TIME_SYNC_INTERVAL_S)
        try:
            await binance.futures.sync_time()
        except Exception as exc:
            typer.echo(f"time sync failed: {exc}")


# Sessions.


@session_app.command("start")
def session_start(ctx: typer.Context) -> None:
    """Start the bot and keep its heartbeat alive while this session lasts (blocking)."""
    env = _env(ctx)
    paths = Paths(env)
    if not Settings().for_env(env).has_credentials:
        typer.echo(f"No {env} API credentials in .env.", err=True)
        raise typer.Exit(2)
    if bot_running(paths.pid):
        typer.echo("A bot is already running; stop it first with `mb session stop`.", err=True)
        raise typer.Exit(1)
    heartbeat = Heartbeat(paths.heartbeat)
    heartbeat.touch()
    pid = spawn_bot(env, paths.pid, paths.log)
    process = psutil.Process(pid)
    try:
        process.wait(timeout=5)
    except psutil.TimeoutExpired:
        pass  # still running, as it should be
    else:
        typer.echo(f"The bot exited at start; see {paths.log}.", err=True)
        raise typer.Exit(1)
    typer.echo(f"Session started ({env}); bot pid {pid}, log {paths.log}.")
    while True:
        reason = keep_alive(heartbeat, process, echo=typer.echo)
        store = Store(paths.store)
        if reason != "bot exited" or not store.get("restart_pending"):
            break
        # `mb restart`: the bot exited with its positions kept; start it on the current code.
        store.put("restart_pending", False)
        heartbeat.touch()
        process = psutil.Process(spawn_bot(env, paths.pid, paths.log))
        store.log("bot_restarted", pid=process.pid)
        typer.echo(f"Bot restarted; pid {process.pid}.")
    typer.echo(f"Heartbeat stopped: {reason}.")


@session_app.command("stop")
def session_stop(ctx: typer.Context) -> None:
    """Close every position and order, then stop the bot."""
    paths = Paths(_env(ctx))
    process = bot_running(paths.pid)
    if process is None:
        typer.echo("No bot is running.")
        return
    result = _send(paths, "stop", {"reason": "session stopped"}, timeout_s=60)
    typer.echo(f"Stop: {result}")
    try:
        process.wait(timeout=120)
    except psutil.TimeoutExpired:
        typer.echo("The bot has not exited yet; check `mb status`.", err=True)
        raise typer.Exit(1) from None
    typer.echo("Bot stopped.")


# Reading state.


def status(ctx: typer.Context) -> None:
    """Show equity, the window, risk, positions, and recent events."""
    paths = Paths(_env(ctx))
    store = Store(paths.store)
    snapshot = store.get("status")
    running = bot_running(paths.pid)
    typer.echo(f"Bot: {'running, pid ' + str(running.pid) if running else 'not running'}")
    if snapshot is None:
        typer.echo("No status yet.")
        return
    typer.echo(render_status(snapshot))
    typer.echo("\nRecent events:")
    for event in store.last_events(8):
        typer.echo(f"  {event.ts:%H:%M:%S} {event.kind} {_brief(event.payload)}")


def events(
    ctx: typer.Context,
    limit: Annotated[int, typer.Option(help="How many events.")] = 30,
) -> None:
    """Print the latest journal events."""
    store = Store(Paths(_env(ctx)).store)
    for event in store.last_events(limit):
        typer.echo(f"{event.ts:%Y-%m-%d %H:%M:%S} #{event.id} {event.kind} {_brief(event.payload)}")


def report(
    ctx: typer.Context,
    hours: Annotated[float, typer.Option(help="Report on the last N hours.")] = 24.0,
) -> None:
    """Trades, closes, and the exchange's own PnL accounting (read-only)."""
    from marketbot.reporting import fetch_income, journal_summary, render, summarize_income

    env = _env(ctx)
    end = datetime.now(UTC)
    start = end - timedelta(hours=hours)

    async def run() -> list[dict[str, Any]]:
        async with Binance.from_settings(Settings().for_env(env)) as binance:
            await binance.futures.sync_time()
            return await fetch_income(binance.futures, start, end)

    income = summarize_income(asyncio.run(run()))
    journal = journal_summary(Store(Paths(env).store), start)
    typer.echo(render(start, end, income, journal))


def render_status(snapshot: dict[str, Any]) -> str:
    lines = [f"Updated: {snapshot['time']}"]
    if snapshot.get("error"):
        lines.append(f"Error: {snapshot['error']}")
    if snapshot.get("stopping"):
        lines.append(f"Stopping: {snapshot['stopping']}")
    controls = snapshot.get("controls") or {}
    lines.append(
        f"Controls: paused={controls.get('paused')}, risk scale={controls.get('risk_scale')}, "
        f"heartbeat age={snapshot.get('heartbeat_age_s')} s"
    )
    window = snapshot.get("window")
    if window:
        lines.append(
            f"Window {window['window_id']}: mode {window['mode']}, E0 {_money(window['e0'])}, "
            f"target {_money(window['target'])}, loss line {_money(window['loss_line'])}, "
            f"entry floor {_money(window['entry_floor'])}, trades {window['trades']}, "
            f"closes {window['closes_at']}"
        )
    project = snapshot.get("project")
    if project:
        lines.append(
            f"Project: start {_money(project['starting_equity'])}, paused {project['paused']}, "
            f"loss-line streak {project['defense_streak']}"
        )
    account = snapshot.get("account")
    if account:
        lines.append(
            f"Equity {_money(account['equity'])} (futures {_money(account['margin_balance'])}, "
            f"other wallets {_money(account['other_wallets'])}), available "
            f"{_money(account['available_balance'])}, open risk {_money(account['open_risk'])}"
        )
        for position in account["positions"]:
            lines.append(
                f"  {position['symbol']} {position['side']} {position['qty']} @ mark "
                f"{position['mark']}, stop {position['stop']} [{position['bucket']}]"
            )
    return "\n".join(lines)


# Commands to the running bot.


def pause(ctx: typer.Context) -> None:
    """Stop opening new positions (open positions keep their stops)."""
    typer.echo(_send(Paths(_env(ctx)), "pause", {}))


def resume(ctx: typer.Context) -> None:
    """Allow new positions again."""
    typer.echo(_send(Paths(_env(ctx)), "resume", {}))


def risk_scale(
    ctx: typer.Context,
    scale: Annotated[str, typer.Argument(help="Multiplier on risk per trade, 0 to 1.")],
) -> None:
    """Scale risk per trade down (never above the configured limit)."""
    typer.echo(_send(Paths(_env(ctx)), "risk_scale", {"scale": scale}))


def auto(
    ctx: typer.Context,
    state: Annotated[str, typer.Argument(help="on or off.")],
) -> None:
    """Let the configured strategies trade (on) or only manage risk (off)."""
    if state not in ("on", "off"):
        raise typer.BadParameter("state must be on or off")
    typer.echo(_send(Paths(_env(ctx)), "auto", {"on": state == "on"}))


def propose(
    ctx: typer.Context,
    symbol: Annotated[str, typer.Argument(help="USD-M perpetual, e.g. SOLUSDT.")],
    side: Annotated[str, typer.Argument(help="long or short.")],
    stop: Annotated[str, typer.Option(help="Stop price (required).")],
    target: Annotated[str | None, typer.Option(help="Take-profit price.")] = None,
    why: Annotated[str, typer.Option(help="Rationale for the journal.")] = "",
) -> None:
    """Propose a trade; the bot sizes it under the risk rules or rejects it."""
    if side.lower() not in ("long", "short"):
        raise typer.BadParameter("side must be long or short")
    payload = {"symbol": symbol, "side": side, "stop": stop, "target": target, "why": why}
    typer.echo(_send(Paths(_env(ctx)), "propose", payload, timeout_s=60))


def flatten(ctx: typer.Context) -> None:
    """Close every position and cancel every order (works without a running bot)."""
    env = _env(ctx)
    paths = Paths(env)
    if bot_running(paths.pid):
        typer.echo(_send(paths, "flatten", {"reason": "manual flatten"}, timeout_s=60))
        return
    typer.echo("No bot is running; closing directly.")
    store = Store(paths.store)

    async def run() -> bool:
        settings = Settings().for_env(env)
        async with Binance.from_settings(settings) as binance:
            await binance.futures.sync_time()
            return await Executor(BinanceFutures(binance.futures), store.log).flatten("manual")

    flat = asyncio.run(run())
    if flat:
        for symbol in store.positions():
            store.delete_position(symbol)
    typer.echo("Flat." if flat else "Not flat; check the account.")
    if not flat:
        raise typer.Exit(1)


def _send(paths: Paths, kind: str, payload: dict[str, Any], timeout_s: float = 30) -> str:
    if bot_running(paths.pid) is None:
        typer.echo("No bot is running; start one with `mb session start`.", err=True)
        raise typer.Exit(1)
    store = Store(paths.store)
    command_id = store.enqueue(kind, payload)
    command = store.wait(command_id, timeout_s)
    if command is None:
        typer.echo(f"No answer within {timeout_s:.0f} s; the command stays queued.", err=True)
        raise typer.Exit(1)
    return f"{command.status}: {_brief(command.result or {})}"


def _money(value: Any) -> str:
    if value is None:
        return "n/a"
    return f"{Decimal(str(value)):.2f}"


def _brief(payload: dict[str, Any], limit: int = 300) -> str:
    text = ", ".join(f"{key}={value}" for key, value in payload.items())
    return text if len(text) <= limit else text[: limit - 3] + "..."


def demo_shift(
    ctx: typer.Context,
    delta: Annotated[
        str, typer.Argument(help="USDT to add to the virtual equity (negative to remove).")
    ],
) -> None:
    """Demo drills only: shift the virtual equity the bot sees, as if PnL happened elsewhere."""
    if _env(ctx) != "demo":
        typer.echo("demo-shift works only with --env demo.", err=True)
        raise typer.Exit(2)
    store = Store(Paths("demo").store)
    offset = store.get("demo_offset")
    if offset is None:
        typer.echo("No demo offset yet; start a demo session first.", err=True)
        raise typer.Exit(1)
    new = Decimal(offset) - Decimal(delta)
    store.put("demo_offset", new)
    store.log("demo_shift", delta=Decimal(delta), offset=new)
    typer.echo(f"Virtual equity shifted by {delta} USDT (offset {new}).")


# Settings and actions for the remote.


@strategies_app.command("show")
def strategies_show(ctx: typer.Context) -> None:
    """Show the configured strategies and the available presets."""
    store = Store(Paths(_env(ctx)).store)
    override = os.environ.get("MB_STRATEGIES")
    typer.echo(f"Configured: {store.get('strategies') or '(none)'}")
    if override:
        typer.echo(f"Overridden in this shell by MB_STRATEGIES: {override}")
    typer.echo(f"On session end: {store.get('on_stop') or 'flatten'}")
    typer.echo("Presets: " + ", ".join(presets.names()))


@strategies_app.command("set")
def strategies_set(
    ctx: typer.Context,
    names: Annotated[str, typer.Argument(help="Comma-separated presets; empty for none.")],
) -> None:
    """Choose the strategies; they take effect at the next start or `mb restart`."""
    unknown = [n for n in names.split(",") if n.strip() and n.strip() not in presets.names()]
    if unknown:
        typer.echo(f"Unknown presets: {', '.join(unknown)}", err=True)
        raise typer.Exit(2)
    store = Store(Paths(_env(ctx)).store)
    store.put("strategies", names)
    store.log("strategies_set", names=names)
    typer.echo(f"Strategies set to '{names}'. Run `mb restart` to apply them now.")


def on_stop(
    ctx: typer.Context,
    mode: Annotated[str, typer.Argument(help="flatten (default) or keep.")],
) -> None:
    """What happens to positions when the session ends; applies at the next (re)start."""
    if mode not in ("flatten", "keep"):
        raise typer.BadParameter("mode must be flatten or keep")
    store = Store(Paths(_env(ctx)).store)
    store.put("on_stop", mode)
    store.log("on_stop_set", mode=mode)
    typer.echo(f"On session end: {mode}. Run `mb restart` to apply it now.")


def restart(ctx: typer.Context) -> None:
    """Restart the bot on the current code, keeping open positions under their stops."""
    paths = Paths(_env(ctx))
    old = bot_running(paths.pid)
    if old is None:
        typer.echo("No bot is running.", err=True)
        raise typer.Exit(1)
    typer.echo(f"Restart: {_send(paths, 'restart', {}, timeout_s=60)}")
    try:
        old.wait(timeout=120)
    except psutil.TimeoutExpired:
        typer.echo("The bot has not exited yet; check `mb status`.", err=True)
        raise typer.Exit(1) from None
    for _ in range(60):
        new = bot_running(paths.pid)
        if new is not None and new.pid != old.pid:
            typer.echo(f"Bot restarted; pid {new.pid}.")
            return
        time.sleep(1)
    typer.echo("No new bot yet: is the session (`mb session start`) still running?", err=True)
    raise typer.Exit(1)


def close(
    ctx: typer.Context,
    symbol: Annotated[str, typer.Argument(help="The perpetual to close, e.g. SOLUSDT.")],
) -> None:
    """Close one position at market and cancel its orders."""
    typer.echo(_send(Paths(_env(ctx)), "close", {"symbol": symbol}, timeout_s=60))


def transfer(
    ctx: typer.Context,
    amount: Annotated[str, typer.Argument(help="Amount to move.")],
    direction: Annotated[str, typer.Argument(help="to-futures or to-spot.")],
    asset: Annotated[str, typer.Option(help="Asset to move.")] = "USDT",
) -> None:
    """Move funds between the spot and USD-M futures wallets (live account only)."""
    env = _env(ctx)
    if env != "live":
        typer.echo("Demo Trading has no wallet transfers.", err=True)
        raise typer.Exit(2)
    kinds = {"to-futures": "MAIN_UMFUTURE", "to-spot": "UMFUTURE_MAIN"}
    if direction not in kinds:
        raise typer.BadParameter("direction must be to-futures or to-spot")

    async def run() -> object:
        async with Binance.from_settings(Settings()) as binance:
            await binance.spot.sync_time()
            return await binance.spot.signed(
                "POST",
                "/sapi/v1/asset/transfer",
                {"type": kinds[direction], "asset": asset.upper(), "amount": amount},
            )

    result = asyncio.run(run())
    Store(Paths(env).store).log("transfer", amount=amount, asset=asset, direction=direction)
    typer.echo(f"Transferred {amount} {asset.upper()} {direction}: {result}")


def ask(
    command: Annotated[str, typer.Argument(help="The mb command to propose, in quotes.")],
    why: Annotated[str, typer.Option(help="What it changes and why.")],
    ttl: Annotated[float, typer.Option(help="Minutes before the proposal expires.")] = 30,
) -> None:
    """Propose a command to the owner on the Telegram remote; it runs only if they tap Run."""
    from marketbot import remote

    try:
        proposal = remote.propose(command, why, timedelta(minutes=ttl))
    except remote.RemoteCommandError as exc:
        typer.echo(f"Not proposable: {exc}", err=True)
        raise typer.Exit(2) from exc
    typer.echo(f"Proposal #{proposal} queued for the remote (expires in {ttl:g} minutes).")


# The Telegram remote.

remote_app = typer.Typer(no_args_is_help=True, help="The Telegram remote for the owner's phone.")
REMOTE_PID = Path("state") / "remote.pid"
REMOTE_LOG = Path("logs") / "remote.log"


def _remote_running() -> psutil.Process | None:
    try:
        process = psutil.Process(int(REMOTE_PID.read_text()))
        cmdline = " ".join(process.cmdline())
    except (FileNotFoundError, ValueError, psutil.Error):
        return None
    return process if "marketbot" in cmdline and "remote run" in cmdline else None


@remote_app.command("run")
def remote_run() -> None:
    """Run the Telegram remote in the foreground."""
    from marketbot import remote as remote_commands
    from marketbot.telegram import Remote, TelegramAPI, journals

    settings = Settings()
    if settings.telegram_bot_token is None:
        typer.echo("No TELEGRAM_BOT_TOKEN in .env.", err=True)
        raise typer.Exit(2)

    async def run() -> None:
        api = TelegramAPI(settings.telegram_bot_token.get_secret_value())
        try:
            await Remote(api, settings.telegram_chat_id, remote_commands.store(), journals()).run()
        finally:
            await api.close()

    asyncio.run(run())


@remote_app.command("start")
def remote_start() -> None:
    """Start the Telegram remote in the background (it outlives this terminal)."""
    from marketbot.session import spawn_detached

    if _remote_running():
        typer.echo("The remote is already running.")
        return
    if Settings().telegram_bot_token is None:
        typer.echo("No TELEGRAM_BOT_TOKEN in .env.", err=True)
        raise typer.Exit(2)
    pid = spawn_detached(
        [sys.executable, "-m", "marketbot", "remote", "run"], REMOTE_PID, REMOTE_LOG
    )
    typer.echo(f"Remote started; pid {pid}, log {REMOTE_LOG}.")


@remote_app.command("stop")
def remote_stop() -> None:
    """Stop the Telegram remote (sessions it started keep running until stopped)."""
    process = _remote_running()
    if process is None:
        typer.echo("The remote is not running.")
        return
    process.terminate()
    typer.echo("Remote stopped.")


@remote_app.command("status")
def remote_status() -> None:
    """Show whether the remote runs and the latest proposals."""
    from marketbot import remote as remote_commands

    process = _remote_running()
    typer.echo(f"Remote: {'running, pid ' + str(process.pid) if process else 'not running'}")
    store = remote_commands.store()
    for event in store.last_events(5):
        typer.echo(f"  {event.ts:%H:%M} {event.kind} {event.payload}")
