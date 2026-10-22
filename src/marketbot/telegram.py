"""The Telegram remote: notifications, Claude's proposals, and the owner's `mb` commands.

The remote is the owner's control panel on the phone. It runs on the owner's computer,
answers only the owner's chat, and executes nothing but the `mb` program:

- notifications from the live and demo bots' journals;
- proposals queued by Claude (`mb ask`), run only when the owner taps Run;
- commands the owner types (`/mb ...`), checked by `marketbot.remote`;
- a second tap for anything that closes positions, stops trading, or moves money.

Claude never runs live trading commands itself; the remote runs them for the owner.
"""

import asyncio
import html
import secrets
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx

from marketbot import remote
from marketbot.config import state_dir
from marketbot.session import bot_running
from marketbot.state.store import Store

API = "https://api.telegram.org"
OUTPUT_LIMIT = 3500
COMMAND_TIMEOUT_S = 180
CONFIRM_TTL = timedelta(minutes=5)

NOTIFY = {
    "entry_filled",
    "position_closed",
    "position_gone",
    "mode_changed",
    "stop_failed",
    "strategy_error",
    "reconcile_unknown_position",
    "session_lost",
    "engine_started",
    "engine_stopped",
    "engine_refused",
    "project_paused",
    "delisting_announced",
    "flatten_error",
    "protect",
    "bot_restarted",
}

HELP = """TheMarketProject remote.

Type any allowed mb command, for example:
/mb status
/mb --env demo status
/mb report --hours 24
/mb pause    /mb resume
/mb risk-scale 0.5
/mb auto on
/mb strategies set delisting-short-4h
/mb restart
/mb close SOLUSDT
/mb flatten
/mb session start    /mb session stop
/mb transfer 50 to-futures

Proposals from Claude arrive with Run and Decline buttons.
Closing positions, stopping, and transfers ask for a second tap.
Free text is not read here: talk to Claude in the Claude app."""


class TelegramError(Exception):
    pass


class TelegramAPI:
    def __init__(self, token: str, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._token = token
        self._client = httpx.AsyncClient(
            base_url=f"{API}/bot{token}", timeout=40, transport=transport
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def call(self, method: str, attempts: int = 3, **params: Any) -> Any:
        """Call the Bot API, retrying network errors: a pooled connection that Telegram has
        closed fails once with ReadError, and a lost reply would hide a command's result."""
        for attempt in range(attempts):
            try:
                response = await self._client.post(f"/{method}", json=params)
                data = response.json()
                break
            except httpx.TransportError as exc:
                if attempt + 1 == attempts:
                    raise TelegramError(
                        self.redact(f"{method}: {type(exc).__name__}: {exc}")
                    ) from None
                await asyncio.sleep(1 + attempt)
            except (httpx.HTTPError, ValueError) as exc:
                raise TelegramError(self.redact(f"{method}: {type(exc).__name__}: {exc}")) from None
        if not data.get("ok"):
            raise TelegramError(self.redact(f"{method}: {data.get('description')}"))
        return data["result"]

    def redact(self, text: str) -> str:
        """Never let the bot token reach a log or a message."""
        return text.replace(self._token, "<token>")

    async def updates(self, offset: int, wait_s: int = 25) -> list[dict[str, Any]]:
        """Long polling: Telegram holds the request up to `wait_s` seconds."""
        return await self.call("getUpdates", offset=offset, timeout=wait_s)

    async def send(
        self,
        chat_id: int,
        text: str,
        buttons: list[list[dict[str, str]]] | None = None,
        *,
        monospace: bool = False,
    ) -> dict[str, Any]:
        """Send text; `monospace` keeps column alignment (command output, tables)."""
        params: dict[str, Any] = {"chat_id": chat_id, "text": text[:4000]}
        if monospace:
            params["text"] = f"<pre>{html.escape(text[-3900:])}</pre>"
            params["parse_mode"] = "HTML"
        if buttons:
            params["reply_markup"] = {"inline_keyboard": buttons}
        return await self.call("sendMessage", **params)

    async def answer(self, callback_id: str, text: str = "") -> None:
        await self.call("answerCallbackQuery", callback_query_id=callback_id, text=text)

    async def clear_buttons(self, chat_id: int, message_id: int) -> None:
        await self.call(
            "editMessageReplyMarkup",
            chat_id=chat_id,
            message_id=message_id,
            reply_markup={"inline_keyboard": []},
        )


Runner = Callable[[remote.RemoteCommand], Any]


async def run_mb(command: remote.RemoteCommand) -> str:
    """Run `python -m marketbot <args>` (never a shell) and return its output."""
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "marketbot",
        *command.args,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    if command.blocking:
        return f"Started `{command.text()}` (pid {process.pid}). Check /mb status in a minute."
    try:
        output, _ = await asyncio.wait_for(process.communicate(), COMMAND_TIMEOUT_S)
    except TimeoutError:
        process.kill()
        return f"`{command.text()}` timed out after {COMMAND_TIMEOUT_S} s."
    text = output.decode("utf-8", errors="replace").strip() or "(no output)"
    code = process.returncode
    return f"$ {command.text()}  (exit {code})\n{text[-OUTPUT_LIMIT:]}"


@dataclass
class PendingConfirmation:
    command: remote.RemoteCommand
    expires: datetime
    proposal_id: int | None = None


@dataclass
class Remote:
    api: TelegramAPI
    chat_id: int | None
    proposals: Store
    journals: dict[str, Path]  # env -> bot.db
    runner: Runner = run_mb
    clock: Callable[[], datetime] = lambda: datetime.now(UTC)
    states: Callable[[], list["BotState"]] = lambda: []
    healthcheck_url: str | None = None
    offset: int = 0
    confirmations: dict[str, PendingConfirmation] = field(default_factory=dict)

    # Main loop.

    async def run(self) -> None:
        await self.announce_start()
        await asyncio.gather(self._poll_loop(), self._push_loop(), self._health_loop())

    async def announce_start(self) -> None:
        """Say what runs; offer to restart a session that should run but does not (a reboot)."""
        if self.chat_id is None:
            return
        lines = ["Remote started. /help for commands."]
        buttons = []
        for state in self.states():
            positions = ", ".join(state.positions) if state.positions else "none"
            if state.running:
                lines.append(f"{state.env}: bot running; positions: {positions}")
            elif state.desired:
                lines.append(
                    f"{state.env}: bot NOT running, but its session was on (computer restart or "
                    f"crash?). Positions on record: {positions}; they keep their exchange stops."
                )
                buttons.append([button(f"Start {state.env} session", f"session:{state.env}")])
            else:
                lines.append(f"{state.env}: no session")
        await self.api.send(self.chat_id, "\n".join(lines), buttons or None)

    async def _health_loop(self) -> None:
        """Ping an external monitor so that a dead computer raises an alert by itself."""
        if not self.healthcheck_url:
            return
        async with httpx.AsyncClient(timeout=10) as client:
            while True:
                try:
                    await client.get(self.healthcheck_url)
                except httpx.HTTPError as exc:
                    print(f"{self.clock():%H:%M:%S} health ping failed: {type(exc).__name__}")
                await asyncio.sleep(60)

    async def _poll_loop(self) -> None:
        while True:
            try:
                for update in await self.api.updates(self.offset):
                    self.offset = update["update_id"] + 1
                    await self.handle_update(update)
            except TelegramError as exc:
                print(f"{self.clock():%H:%M:%S} telegram: {exc}", flush=True)
                await asyncio.sleep(5)

    async def _push_loop(self) -> None:
        while True:
            try:
                await self.push_events()
                await self.push_proposals()
            except TelegramError as exc:
                print(f"{self.clock():%H:%M:%S} telegram: {exc}", flush=True)
            await asyncio.sleep(5)

    # Incoming.

    async def handle_update(self, update: dict[str, Any]) -> None:
        if "callback_query" in update:
            await self._handle_callback(update["callback_query"])
            return
        message = update.get("message") or {}
        chat = (message.get("chat") or {}).get("id")
        text = (message.get("text") or "").strip()
        if chat is None or not text:
            return
        if self.chat_id is None:
            await self.api.send(
                chat,
                f"This chat's id is {chat}. Put TELEGRAM_CHAT_ID={chat} into .env on the bot's "
                "computer and restart the remote (`mb remote stop`, `mb remote start`).",
            )
            return
        if chat != self.chat_id:
            return  # strangers get no answer at all
        if text in ("/start", "/help"):
            await self.api.send(chat, HELP)
        elif text.startswith("/mb"):
            await self._handle_command(text)
        else:
            await self.api.send(chat, "Only /mb commands here. Talk to Claude in the Claude app.")

    async def _handle_command(self, text: str, proposal_id: int | None = None) -> None:
        assert self.chat_id is not None
        try:
            command = remote.parse(text)
        except remote.RemoteCommandError as exc:
            await self.api.send(self.chat_id, f"Not run: {exc}")
            return
        if command.dangerous:
            await self._ask_confirmation(command, proposal_id)
            return
        await self._execute(command, proposal_id)

    async def _ask_confirmation(
        self, command: remote.RemoteCommand, proposal_id: int | None
    ) -> None:
        assert self.chat_id is not None
        token = secrets.token_hex(4)
        self.confirmations[token] = PendingConfirmation(
            command, self.clock() + CONFIRM_TTL, proposal_id
        )
        await self.api.send(
            self.chat_id,
            f"Confirm: {command.text()}\nThis closes positions, stops trading, or moves money.",
            [[button("Yes, run it", f"confirm:{token}"), button("Cancel", f"cancel:{token}")]],
        )

    async def _execute(self, command: remote.RemoteCommand, proposal_id: int | None) -> None:
        assert self.chat_id is not None
        output = await self.runner(command)
        if proposal_id is not None:
            self.proposals.complete(proposal_id, "done", {"output": output[-1000:]})
        await self.api.send(self.chat_id, output, monospace=True)

    async def _handle_callback(self, callback: dict[str, Any]) -> None:
        message = callback.get("message") or {}
        chat = (message.get("chat") or {}).get("id")
        if self.chat_id is None or chat != self.chat_id:
            return
        await self.api.answer(callback["id"])
        action, _, key = str(callback.get("data", "")).partition(":")
        await self.api.clear_buttons(chat, message["message_id"])
        if action in ("confirm", "cancel"):
            pending = self.confirmations.pop(key, None)
            if pending is None or self.clock() >= pending.expires:
                await self.api.send(chat, "This confirmation has expired; nothing was run.")
            elif action == "cancel":
                if pending.proposal_id is not None:
                    self.proposals.complete(pending.proposal_id, "declined", {})
                await self.api.send(chat, f"Cancelled: {pending.command.text()}")
            else:
                await self._execute(pending.command, pending.proposal_id)
            return
        if action in ("run", "decline"):
            await self._handle_proposal(int(key), action)
        if action == "session" and key in ("live", "demo"):
            await self._execute(remote.parse(f"--env {key} session start"), None)

    async def _handle_proposal(self, proposal_id: int, action: str) -> None:
        assert self.chat_id is not None
        proposal = self.proposals.command(proposal_id)
        if proposal is None or proposal.status != "sent":
            await self.api.send(self.chat_id, f"Proposal #{proposal_id} is no longer open.")
            return
        if remote.expired(proposal.payload, self.clock()):
            self.proposals.complete(proposal_id, "expired", {})
            await self.api.send(self.chat_id, f"Proposal #{proposal_id} expired; nothing was run.")
            return
        if action == "decline":
            self.proposals.complete(proposal_id, "declined", {})
            await self.api.send(self.chat_id, f"Declined proposal #{proposal_id}.")
            return
        await self._handle_command(proposal.payload["command"], proposal_id)

    # Outgoing.

    async def push_proposals(self) -> None:
        if self.chat_id is None:
            return
        for proposal in self.proposals.pending():
            payload = proposal.payload
            if remote.expired(payload, self.clock()):
                self.proposals.complete(proposal.id, "expired", {})
                continue
            expires = datetime.fromisoformat(payload["expires"])
            await self.api.send(
                self.chat_id,
                f"Proposal #{proposal.id} from Claude\n{payload['why']}\n\n"
                f"Command: {payload['command']}\nValid until {expires:%H:%M} UTC",
                [
                    [
                        button("Run", f"run:{proposal.id}"),
                        button("Decline", f"decline:{proposal.id}"),
                    ]
                ],
            )
            self.proposals.complete(proposal.id, "sent", {})

    async def push_events(self) -> None:
        if self.chat_id is None:
            return
        for env, path in self.journals.items():
            if not path.exists():
                continue
            journal = Store(path)
            key = f"last_event:{env}"
            last = self.proposals.get(key)
            if last is None:  # start from now, not from the whole history
                events = journal.last_events(1)
                self.proposals.put(key, events[-1].id if events else 0)
                continue
            lines = []
            for event in journal.events(after_id=last, limit=200):
                last = event.id
                if event.kind in NOTIFY:
                    lines.append(describe(event.kind, event.payload, event.ts))
            self.proposals.put(key, last)
            if lines:
                await self.api.send(self.chat_id, f"[{env.upper()}]\n" + "\n".join(lines))


@dataclass(frozen=True)
class BotState:
    env: str
    running: bool
    desired: bool  # a session was started and not stopped by the owner
    positions: list[str]


def bot_states(envs: dict[str, Path]) -> list[BotState]:
    states = []
    for env, db in envs.items():
        if not db.exists():
            continue
        store = Store(db)
        states.append(
            BotState(
                env=env,
                running=bot_running(db.parent / "bot.pid") is not None,
                desired=bool(store.get("session_desired")),
                positions=sorted(store.positions()),
            )
        )
    return states


def button(text: str, data: str) -> dict[str, str]:
    return {"text": text, "callback_data": data}


def describe(kind: str, payload: dict[str, Any], ts: datetime) -> str:
    time = f"{ts:%H:%M}"
    match kind:
        case "entry_filled":
            return (
                f"{time} entry {payload.get('side')} {payload.get('qty')} {payload.get('symbol')}"
                f" @ {payload.get('price')} ({payload.get('strategy')})"
            )
        case "position_closed":
            return f"{time} closed {payload.get('symbol')}: {payload.get('reason')}"
        case "position_gone":
            return f"{time} {payload.get('symbol')} closed on the exchange (stop or target)"
        case "mode_changed":
            return (
                f"{time} mode {payload.get('previous')} -> {payload.get('current')}: "
                f"{payload.get('reason')}, equity {payload.get('equity')}"
            )
        case "protect":
            return f"{time} PROTECT: stops tightened to the floor {payload.get('floor')}"
        case _:
            return f"{time} {kind} {str(payload)[:200]}"


def journals() -> dict[str, Path]:
    return {env: state_dir(env) / "bot.db" for env in ("live", "demo")}
