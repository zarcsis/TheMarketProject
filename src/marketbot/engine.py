"""The bot's main loop: reconcile with the exchange, enforce the risk modes, run commands.

Each tick:
1. Checks the session heartbeat. If the session is gone, closes everything and stops.
2. Reads the account and updates the window ledger and project breakers.
3. Enforces the mode: DEFENSE and HALTED hold no positions; PROTECT keeps every stop
   above the lock floor.
4. Reconciles: every position has a stop, unknown positions are closed, orphan orders
   are cancelled, closed positions are recorded.
5. Runs queued commands from the `mb` CLI. Entries go through the risk manager.
6. Writes a status snapshot for `mb status`.
"""

import asyncio
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from marketbot.domain import Side
from marketbot.exchange.futures import AccountState, Exchange, Position
from marketbot.execution.executor import EXCHANGE_ERRORS, EntryFailed, Executor, ManagedPosition
from marketbot.risk.config import RiskConfig
from marketbot.risk.project import ProjectState, check_equity, close_window, risk_multiplier
from marketbot.risk.sizing import (
    OpenPosition,
    SymbolRules,
    TradeIntent,
    entry_floor,
    size_trade,
    tighten_stops,
    total_risk,
)
from marketbot.risk.window import (
    ENTRY_MODES,
    FLAT_MODES,
    Mode,
    WindowState,
    open_window,
    record_trade,
    update_window,
)
from marketbot.state.store import Command, Store, load_dataclass
from marketbot.strategy.runtime import Action, Close, MoveStop, PositionView, Propose, Strategy

STALE_DATA = timedelta(seconds=60)
RESTART = "restart"


class Heartbeat:
    """A file the session touches; the bot stops trading when it goes stale."""

    def __init__(self, path: Path, timeout: timedelta = timedelta(seconds=120)) -> None:
        self.path = path
        self.timeout = timeout

    def touch(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(datetime.now(UTC).isoformat(), encoding="utf-8")

    def age(self, now: datetime) -> timedelta | None:
        try:
            modified = datetime.fromtimestamp(self.path.stat().st_mtime, UTC)
        except FileNotFoundError:
            return None
        return now - modified

    def alive(self, now: datetime) -> bool:
        age = self.age(now)
        return age is not None and age <= self.timeout


@dataclass(frozen=True)
class Controls:
    paused: bool = False
    risk_scale: Decimal = Decimal(1)
    auto: bool = False  # whether strategies may act; off until `mb auto on`


class Engine:
    def __init__(
        self,
        exchange: Exchange,
        store: Store,
        config: RiskConfig,
        *,
        heartbeat: Heartbeat | None = None,
        strategies: Sequence[Strategy] = (),
        keep_positions_on_stop: bool = False,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.strategies = list(strategies)
        # When the session ends: close everything (the default), or leave positions open
        # with their exchange-side stops for the next session (multi-day strategies).
        self.keep_positions_on_stop = keep_positions_on_stop
        self.exchange = exchange
        self.store = store
        self.config = config
        self.heartbeat = heartbeat
        self.clock = clock
        self.executor = Executor(exchange, store.log)
        self.last_account: AccountState | None = None
        self.last_ok: datetime | None = None
        self.stopping: str | None = None  # reason, once the engine is shutting down
        self.failing_since: datetime | None = None  # first failed account read in a row
        self.access_lost = False

    # State in the store.

    @property
    def window(self) -> WindowState | None:
        data = self.store.get("window")
        return load_dataclass(WindowState, data) if data else None

    @property
    def project(self) -> ProjectState | None:
        data = self.store.get("project")
        return load_dataclass(ProjectState, data) if data else None

    @property
    def controls(self) -> Controls:
        data = self.store.get("controls")
        return load_dataclass(Controls, data) if data else Controls()

    def managed(self) -> dict[str, ManagedPosition]:
        return {
            symbol: load_dataclass(ManagedPosition, data)
            for symbol, data in self.store.positions().items()
        }

    # The loop.

    async def run(self, interval_s: float = 5.0) -> str:
        """Tick until the engine stops; returns the reason."""
        self.store.log("engine_started")
        while True:
            if not await self.tick():
                assert self.stopping is not None
                self.store.log("engine_stopped", reason=self.stopping)
                return self.stopping
            await asyncio.sleep(interval_s)

    async def tick(self) -> bool:
        """One cycle; False once the engine has stopped and the account is flat."""
        now = self.clock()
        if self.heartbeat is not None and not self.heartbeat.alive(now) and not self.stopping:
            self.stopping = "session lost"
            self.store.log("session_lost", heartbeat_age_s=_seconds(self.heartbeat.age(now)))
        if self.stopping:
            keep = self.keep_positions_on_stop or self.stopping == RESTART
            if keep and not self._must_be_flat():
                secured = await self._secure_for_handover(now)
                self._write_status(now, error=None if secured else "securing stops; retrying")
                return not secured
            flat = await self.executor.flatten(self.stopping)
            self._write_status(now, error=None if flat else "flatten incomplete; retrying")
            return not flat

        account = await self._fetch_account(now)
        if account is None:
            return True
        window = self._update_window(account, now)
        if await self._enforce_mode(window, account):
            account = await self._fetch_account(now)
            if account is None:
                return True
        await self._reconcile(account)
        await self._run_commands(now)
        await self._run_strategies(now)
        self._write_status(now)
        if self.stopping:
            return await self.tick()  # a stop command: close everything now, not next tick
        return True

    def _must_be_flat(self) -> bool:
        window, project = self.window, self.project
        return (window is not None and window.mode in FLAT_MODES) or (
            project is not None and project.paused
        )

    async def _secure_for_handover(self, now: datetime) -> bool:
        """Leave only positions that the exchange protects; True once that holds."""
        account = await self._fetch_account(now)
        if account is None:
            return False
        await self._reconcile(account)  # restores missing stops, closes what it cannot protect
        account = await self._fetch_account(now)
        if account is None:
            return False
        unprotected = [p for p in account.positions.values() if not account.stops_for(p)]
        if unprotected:
            return False
        self.store.log(
            "positions_kept",
            reason=self.stopping,
            positions={s: str(p.qty) for s, p in account.positions.items()},
        )
        return True

    async def _fetch_account(self, now: datetime) -> AccountState | None:
        try:
            account = await self.exchange.account()
        except EXCHANGE_ERRORS as exc:
            stale = self.last_ok is None or now - self.last_ok > STALE_DATA
            self.store.log("account_error", error=str(exc), stale=stale)
            self._write_status(now, error=f"account unavailable: {exc}")
            self.failing_since = self.failing_since or now
            if not self.access_lost and now - self.failing_since >= STALE_DATA:
                # One alert per outage (the remote forwards it), not one per tick.
                self.access_lost = True
                self.store.log("account_access_lost", error=str(exc), since=self.failing_since)
            return None
        if self.access_lost:
            self.store.log("account_access_restored", lost_since=self.failing_since)
        self.failing_since, self.access_lost = None, False
        self.last_account, self.last_ok = account, now
        return account

    # Window and project.

    def _update_window(self, account: AccountState, now: datetime) -> WindowState:
        equity = account.equity
        window, project = self.window, self.project
        if project is None:
            project = ProjectState(starting_equity=equity)
            self.store.log("project_started", starting_equity=equity)
        if window is None:
            window = open_window(1, equity, now)
            self.store.log("window_opened", window_id=1, e0=equity)
        else:
            update = update_window(window, equity, now, self.config, flat=not account.positions)
            if update.closed is not None:
                project = close_window(project, update.closed, self.config)
                self.store.log("window_closed", window=update.closed, equity=equity)
                self.store.log(
                    "window_opened", window_id=update.state.window_id, e0=update.state.e0
                )
            for transition in update.transitions:
                self.store.log(
                    "mode_changed",
                    previous=transition.previous,
                    current=transition.current,
                    reason=transition.reason,
                    equity=equity,
                )
            window = update.state
        checked = check_equity(project, equity, self.config)
        if checked.paused and not project.paused:
            self.store.log("project_paused", equity=equity, starting=project.starting_equity)
        self.store.put("window", window)
        self.store.put("project", checked)
        return window

    async def _enforce_mode(self, window: WindowState, account: AccountState) -> bool:
        """Act on the mode; True when it changed positions or orders."""
        project = self.project
        must_be_flat = window.mode in FLAT_MODES or (project is not None and project.paused)
        if must_be_flat and (account.positions or account.conditional_orders):
            reason = f"mode {window.mode}" if window.mode in FLAT_MODES else "project paused"
            if await self.executor.flatten(reason):
                for symbol in self.managed():
                    self.store.delete_position(symbol)
            return True
        if window.mode is Mode.PROTECT and account.positions:
            return await self._protect(window, account)
        return False

    async def _protect(self, window: WindowState, account: AccountState) -> bool:
        floor = entry_floor(window, self.config)
        assert floor is not None
        managed = self.managed()
        positions = [p for p in self._open_positions(account, managed) if p.symbol in managed]
        changes = tighten_stops(positions, account.equity, floor, self.config.costs)
        if changes:
            self.store.log("protect", floor=floor, equity=account.equity, changes=changes)
        for change in changes:
            position = managed[change.symbol]
            try:
                if change.stop is None:
                    await self.executor.close(
                        position.symbol, position.side, position.qty, reason="protect lock floor"
                    )
                    self.store.delete_position(position.symbol)
                else:
                    info = await self.exchange.symbol_info(position.symbol)
                    moved = await self.executor.move_stop(position, change.stop, info)
                    self.store.put_position(moved.symbol, moved)
            except EXCHANGE_ERRORS as exc:
                self.store.log("protect_error", symbol=position.symbol, error=str(exc))
        return bool(changes)

    # Reconciliation.

    async def _reconcile(self, account: AccountState) -> None:
        managed = self.managed()

        for symbol, managed_position in managed.items():
            if symbol not in account.positions:
                self.store.delete_position(symbol)
                self.store.log("position_gone", symbol=symbol, stop=managed_position.stop)

        for symbol, position in account.positions.items():
            known = managed.get(symbol)
            try:
                if known is None or known.side is not position.side:
                    self.store.log(
                        "reconcile_unknown_position",
                        symbol=symbol,
                        side=position.side,
                        qty=position.qty,
                    )
                    await self.executor.close(
                        symbol, position.side, position.qty, reason="unknown position"
                    )
                    self.store.delete_position(symbol)
                    continue
                if known.qty != position.qty:
                    known = replace(known, qty=position.qty)
                    self.store.put_position(symbol, known)
                await self._ensure_single_stop(known, position, account)
            except EXCHANGE_ERRORS as exc:
                self.store.log("reconcile_error", symbol=symbol, error=str(exc))

        # Orders on symbols without a position (stops left after a close, stray orders).
        flat_symbols = (
            {order.symbol for order in account.conditional_orders} | account.open_order_symbols
        ) - set(account.positions)
        for symbol in flat_symbols:
            try:
                await self.exchange.cancel_all(symbol)
                self.store.log("orphan_orders_cancelled", symbol=symbol)
            except EXCHANGE_ERRORS as exc:
                self.store.log("reconcile_error", symbol=symbol, error=str(exc))

    async def _ensure_single_stop(
        self, known: ManagedPosition, position: Position, account: AccountState
    ) -> None:
        stops = account.stops_for(position)
        if not stops:
            self.store.log("stop_missing", symbol=known.symbol, stop=known.stop)
            info = await self.exchange.symbol_info(known.symbol)
            try:
                restored = await self.executor.place_stop(known, info)
            except EXCHANGE_ERRORS as exc:
                self.store.log("stop_failed", symbol=known.symbol, error=str(exc))
                await self.executor.close(
                    known.symbol, known.side, position.qty, reason="stop could not be restored"
                )
                self.store.delete_position(known.symbol)
                return
            self.store.put_position(known.symbol, restored)
            return
        keep = next((s for s in stops if s.algo_id == known.stop_algo_id), stops[0])
        for extra in stops:
            if extra.algo_id != keep.algo_id:
                await self.exchange.cancel_conditional(extra.symbol, extra.algo_id)
                self.store.log("extra_stop_cancelled", symbol=extra.symbol, algo_id=extra.algo_id)
        known = replace(known, qty=position.qty)
        if keep.algo_id != known.stop_algo_id or keep.trigger_price != known.stop:
            known = replace(known, stop=keep.trigger_price, stop_algo_id=keep.algo_id)
            self.store.put_position(known.symbol, known)
        if keep.reduce_only and keep.qty != position.qty:
            # A reduce-only stop must cover the whole position; re-place it at the same price.
            info = await self.exchange.symbol_info(known.symbol)
            self.store.put_position(
                known.symbol, await self.executor.move_stop(known, known.stop, info)
            )

    # Commands.

    async def _run_commands(self, now: datetime) -> None:
        for command in self.store.pending():
            try:
                status, result = await self._run_command(command, now)
            except Exception as exc:  # a bad command must not stop the loop
                status, result = "error", {"error": f"{type(exc).__name__}: {exc}"}
            self.store.complete(command.id, status, result)
            self.store.log(
                "command", id=command.id, kind=command.kind, status=status, result=result
            )

    async def _run_command(self, command: Command, now: datetime) -> tuple[str, dict[str, Any]]:
        payload = command.payload
        match command.kind:
            case "propose":
                return await self._propose(payload)
            case "pause" | "resume":
                controls = replace(self.controls, paused=command.kind == "pause")
                self.store.put("controls", controls)
                return "done", asdict(controls)
            case "risk_scale":
                scale = Decimal(str(payload["scale"]))
                if not Decimal(0) <= scale <= 1:
                    return "rejected", {"error": "risk scale must be between 0 and 1"}
                controls = replace(self.controls, risk_scale=scale)
                self.store.put("controls", controls)
                return "done", asdict(controls)
            case "auto":
                controls = replace(self.controls, auto=bool(payload["on"]))
                self.store.put("controls", controls)
                return "done", asdict(controls)
            case "flatten":
                flat = await self.executor.flatten(payload.get("reason", "manual"))
                if flat:
                    for symbol in self.managed():
                        self.store.delete_position(symbol)
                return ("done" if flat else "error"), {"flat": flat}
            case "stop":
                self.stopping = payload.get("reason", "session stopped")
                return "done", {"stopping": self.stopping}
            case "restart":
                # Exit with positions kept under their stops; the session starts a new bot.
                self.stopping = RESTART
                self.store.put("restart_pending", True)
                return "done", {"stopping": RESTART}
            case "close":
                return await self._close_symbol(str(payload["symbol"]).upper())
            case _:
                return "rejected", {"error": f"unknown command {command.kind!r}"}

    async def _close_symbol(self, symbol: str) -> tuple[str, dict[str, Any]]:
        account = await self.exchange.account()
        position = account.positions.get(symbol)
        if position is None:
            return "rejected", {"error": f"no open position in {symbol}"}
        await self.executor.close(symbol, position.side, position.qty, reason="manual close")
        self.store.delete_position(symbol)
        await self.exchange.cancel_all(symbol)
        return "done", {"closed": symbol, "qty": position.qty}

    async def _propose(self, payload: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        window, project, controls = self.window, self.project, self.controls
        account = self.last_account
        if window is None or project is None or account is None:
            return "rejected", {"error": "no account state yet"}
        if controls.paused:
            return "rejected", {"error": "trading is paused"}
        symbol = str(payload["symbol"]).upper()
        side = Side(str(payload["side"]).upper())
        info = await self.exchange.symbol_info(symbol)
        entry = await self.exchange.mark_price(symbol)
        intent = TradeIntent(
            symbol=symbol,
            side=side,
            entry=entry,
            stop=Decimal(str(payload["stop"])),
            bucket=payload.get("bucket", "btc_beta"),
            strategy=payload.get("strategy", "manual"),
        )
        decision = size_trade(
            intent,
            window,
            account.equity,
            self._open_positions(account, self.managed()),
            SymbolRules(
                step_size=info.step_size,
                min_qty=info.min_qty,
                min_notional=info.min_notional,
                maintenance_margin_rate=info.maintenance_margin_rate,
            ),
            account.available_balance,
            self.config,
            risk_scale=controls.risk_scale,
            project_multiplier=risk_multiplier(project, self.config),
        )
        self.store.log(
            "decision", intent=intent, decision=decision, why=payload.get("why", ""), entry=entry
        )
        if not decision.approved:
            return "rejected", {"reasons": list(decision.reasons)}
        target = Decimal(str(payload["target"])) if payload.get("target") else None
        try:
            position = await self.executor.enter(intent, decision, info, target=target)
        except EntryFailed as exc:
            return "error", {"error": str(exc)}
        self.store.put_position(symbol, position)
        self.store.put("window", record_trade(window))
        # The next entry in this tick must see this position: its limits and the open-risk
        # budget are checked against the account. Found on Demo Trading, where four entries
        # in one tick all passed against the same stale snapshot.
        if await self._fetch_account(self.clock()) is None:
            self.last_account = None  # no fresh state: no more entries until the next tick
        return "done", {"position": asdict(position), "risk": decision.risk}

    # Strategies.

    async def _run_strategies(self, now: datetime) -> None:
        controls = self.controls
        if not self.strategies or not controls.auto or controls.paused or self.stopping:
            return
        views = {
            symbol: PositionView(
                symbol=symbol,
                side=position.side,
                entry_price=position.entry_price,
                stop=position.stop,
                initial_risk=abs(position.entry_price - (position.initial_stop or position.stop)),
                opened_at=position.opened_at or now,
                strategy=position.strategy,
            )
            for symbol, position in self.managed().items()
        }
        for strategy in self.strategies:
            try:
                actions = await strategy.step(now, views)
            except Exception as exc:  # a strategy failure must not stop risk management
                self.store.log("strategy_error", strategy=strategy.name, error=repr(exc))
                continue
            for action in actions:
                try:
                    await self._apply(action)
                except Exception as exc:
                    self.store.log("strategy_error", action=action, error=repr(exc))

    async def _apply(self, action: Action) -> None:
        managed = self.managed().get(action.symbol)
        match action:
            case Propose():
                window = self.window
                if window is None or window.mode not in ENTRY_MODES:
                    return
                status, result = await self._propose(
                    {
                        "symbol": action.symbol,
                        "side": action.side.value,
                        "stop": action.stop,
                        "target": action.target,
                        "strategy": action.strategy,
                        "why": action.why,
                        "bucket": action.bucket,
                    }
                )
                self.store.log("strategy_entry", status=status, result=result, why=action.why)
            case MoveStop() if managed is not None:
                direction = 1 if managed.side is Side.LONG else -1
                if (action.stop - managed.stop) * direction <= 0:
                    return  # stops only tighten
                account = self.last_account
                position = account.positions.get(action.symbol) if account else None
                if position is not None and (position.mark_price - action.stop) * direction <= 0:
                    # The new stop is already through the market: the trade is stopped out.
                    await self.executor.close(
                        managed.symbol, managed.side, managed.qty, reason=action.why
                    )
                    self.store.delete_position(managed.symbol)
                    return
                info = await self.exchange.symbol_info(action.symbol)
                moved = await self.executor.move_stop(managed, action.stop, info)
                self.store.put_position(moved.symbol, moved)
            case Close() if managed is not None:
                await self.executor.close(
                    managed.symbol, managed.side, managed.qty, reason=action.why
                )
                self.store.delete_position(managed.symbol)

    # Helpers.

    def _open_positions(
        self, account: AccountState, managed: dict[str, ManagedPosition]
    ) -> list[OpenPosition]:
        positions = []
        for symbol, position in account.positions.items():
            known = managed.get(symbol)
            stops = account.stops_for(position)
            stop = known.stop if known else (stops[0].trigger_price if stops else None)
            positions.append(
                OpenPosition(
                    symbol=symbol,
                    side=position.side,
                    qty=position.qty,
                    mark=position.mark_price,
                    stop=stop,
                    bucket=known.bucket if known else "btc_beta",
                )
            )
        return positions

    def _write_status(self, now: datetime, error: str | None = None) -> None:
        window, project, account = self.window, self.project, self.last_account
        status: dict[str, Any] = {
            "time": now,
            "stopping": self.stopping,
            "error": error,
            "controls": self.controls,
            "heartbeat_age_s": _seconds(self.heartbeat.age(now)) if self.heartbeat else None,
        }
        if window is not None:
            limits = self.config.window
            status["window"] = {
                **asdict(window),
                "target": window.target(limits),
                "loss_line": window.loss_line(limits),
                "entry_floor": entry_floor(window, self.config),
                "closes_at": window.closes_at(limits),
            }
        if project is not None:
            status["project"] = project
        if account is not None:
            positions = self._open_positions(account, self.managed())
            status["account"] = {
                "equity": account.equity,
                "margin_balance": account.margin_balance,
                "available_balance": account.available_balance,
                "other_wallets": account.other_wallets,
                "open_risk": total_risk(positions, self.config.costs),
                "positions": positions,
            }
        self.store.put("status", status)


def _seconds(delta: timedelta | None) -> float | None:
    return round(delta.total_seconds(), 1) if delta is not None else None
