import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from marketbot import remote
from marketbot.state.store import Store
from marketbot.telegram import BotState, Remote, TelegramAPI, TelegramError

pytestmark = pytest.mark.anyio
OWNER = 111
STRANGER = 999
TOKEN = "123456:SECRET-token"


class FakeTelegram:
    def __init__(self) -> None:
        self.sent: list[dict] = []
        self.edited: list[dict] = []
        self.calls: list[str] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        method = request.url.path.rsplit("/", 1)[-1]
        self.calls.append(method)
        body = json.loads(request.content or b"{}")
        if method == "editMessageText":
            self.edited.append(body)
        if method == "sendMessage":
            self.sent.append(body)
            return httpx.Response(200, json={"ok": True, "result": {"message_id": len(self.sent)}})
        return httpx.Response(200, json={"ok": True, "result": True})

    def texts(self) -> list[str]:
        return [m["text"] for m in self.sent]

    def buttons(self) -> list[str]:
        markup = self.sent[-1].get("reply_markup", {}).get("inline_keyboard", [])
        return [b["callback_data"] for row in markup for b in row]


class Rig:
    def __init__(self, tmp_path: Path) -> None:
        self.telegram = FakeTelegram()
        self.api = TelegramAPI(TOKEN, transport=httpx.MockTransport(self.telegram.handler))
        self.ran: list[str] = []
        self.db = tmp_path / "remote.db"
        self.remote = Remote(
            self.api,
            OWNER,
            Store(self.db),
            {"demo": tmp_path / "demo.db"},
            runner=self.run,
        )

    async def run(self, command: remote.RemoteCommand) -> str:
        self.ran.append(command.text())
        return f"ran {command.text()}"

    async def message(self, text: str, chat: int = OWNER) -> None:
        await self.remote.handle_update({"message": {"chat": {"id": chat}, "text": text}})

    async def tap(self, data: str, chat: int = OWNER) -> None:
        callback = {"id": "cb", "data": data, "message": {"chat": {"id": chat}, "message_id": 1}}
        await self.remote.handle_update({"callback_query": callback})


@pytest.fixture
def rig(tmp_path: Path) -> Rig:
    return Rig(tmp_path)


async def test_owner_command_runs(rig: Rig) -> None:
    await rig.message("/mb --env demo status")
    assert rig.ran == ["mb --env demo status"]
    assert rig.telegram.texts()[-1] == "<pre>ran mb --env demo status</pre>"
    assert rig.telegram.sent[-1]["parse_mode"] == "HTML"


async def test_strangers_are_ignored(rig: Rig) -> None:
    await rig.message("/mb flatten", chat=STRANGER)
    await rig.tap("run:1", chat=STRANGER)
    assert rig.ran == []
    assert rig.telegram.sent == []


async def test_disallowed_and_free_text(rig: Rig) -> None:
    await rig.message("/mb keygen")
    await rig.message("close everything please")
    assert rig.ran == []
    assert "Not run" in rig.telegram.texts()[0]
    assert "Claude app" in rig.telegram.texts()[1]


async def test_dangerous_command_needs_a_second_tap(rig: Rig) -> None:
    await rig.message("/mb flatten")
    assert rig.ran == []
    confirm, cancel = rig.telegram.buttons()
    assert confirm.startswith("confirm:") and cancel.startswith("cancel:")
    await rig.tap(confirm)
    assert rig.ran == ["mb flatten"]
    await rig.tap(confirm)  # a confirmation works once
    assert rig.ran == ["mb flatten"]


async def test_proposal_runs_only_when_tapped(rig: Rig) -> None:
    proposal = remote.propose("restart", "fix X tested on demo", timedelta(minutes=30), rig.db)
    await rig.remote.push_proposals()
    assert "fix X tested on demo" in rig.telegram.texts()[-1]
    assert rig.telegram.buttons() == [f"run:{proposal}", f"decline:{proposal}"]
    assert rig.ran == []
    await rig.tap(f"run:{proposal}")
    assert rig.ran == ["mb restart"]
    assert Store(rig.db).command(proposal).status == "done"
    await rig.tap(f"run:{proposal}")  # not twice
    assert rig.ran == ["mb restart"]


async def test_declined_and_expired_proposals_do_not_run(rig: Rig) -> None:
    declined = remote.propose("pause", "maybe", timedelta(minutes=30), rig.db)
    stale = remote.propose("resume", "later", timedelta(minutes=1), rig.db)
    await rig.remote.push_proposals()
    await rig.tap(f"decline:{declined}")
    rig.remote.clock = lambda: datetime.now(UTC) + timedelta(minutes=2)
    await rig.tap(f"run:{stale}")
    assert rig.ran == []
    assert Store(rig.db).command(declined).status == "declined"
    assert Store(rig.db).command(stale).status == "expired"


async def test_events_are_pushed(rig: Rig, tmp_path: Path) -> None:
    journal = Store(tmp_path / "demo.db")
    journal.log("engine_started")
    await rig.remote.push_events()  # starts from now
    journal.log(
        "mode_changed", previous="NORMAL", current="PROTECT", reason="target reached", equity="137"
    )
    journal.log("decision", detail="not notified")
    await rig.remote.push_events()
    assert len(rig.telegram.sent) == 1
    assert "[DEMO]" in rig.telegram.texts()[0]
    assert "NORMAL -> PROTECT" in rig.telegram.texts()[0]


async def test_unknown_chat_learns_its_id_only(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    rig.remote.chat_id = None
    await rig.message("/mb flatten", chat=STRANGER)
    assert rig.ran == []
    assert f"TELEGRAM_CHAT_ID={STRANGER}" in rig.telegram.texts()[0]


async def test_errors_never_contain_the_token() -> None:
    def failing(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"cannot reach {request.url}")

    api = TelegramAPI(TOKEN, transport=httpx.MockTransport(failing))
    with pytest.raises(TelegramError) as error:
        await api.send(OWNER, "hi")
    assert TOKEN not in str(error.value)
    assert "<token>" in str(error.value)


async def test_start_offers_to_restart_an_interrupted_session(rig: Rig) -> None:
    from marketbot.telegram import BotState

    rig.remote.states = lambda: [
        BotState("live", running=False, desired=True, positions=["DOGEUSDT"]),
        BotState("demo", running=False, desired=False, positions=[]),
    ]
    await rig.remote.announce_start()
    text = rig.telegram.texts()[-1]
    assert "live: bot NOT running" in text and "DOGEUSDT" in text
    assert "demo: no session" in text
    assert rig.telegram.buttons() == ["session:live"]
    await rig.tap("session:live")
    assert rig.ran == ["mb --env live session start"]


async def test_start_message_without_interruptions_has_no_buttons(rig: Rig) -> None:
    from marketbot.telegram import BotState

    rig.remote.states = lambda: [BotState("live", running=True, desired=True, positions=[])]
    await rig.remote.announce_start()
    assert "live: bot running" in rig.telegram.texts()[-1]
    assert "reply_markup" not in rig.telegram.sent[-1]


def test_remote_run_wires_states_and_healthcheck(tmp_path: Path, monkeypatch) -> None:
    # Regression: a formatter-collapsed call once dropped these arguments silently.
    import marketbot.telegram as telegram_module
    from marketbot.control import remote_run

    captured = {}

    class FakeRemote:
        def __init__(self, *args, **kwargs) -> None:
            captured.update(kwargs)

        async def run(self) -> None:
            return None

    monkeypatch.chdir(tmp_path)  # no real .env
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", TOKEN)
    monkeypatch.setenv("HEALTHCHECK_URL", "https://hc.example/ping")
    monkeypatch.setattr(telegram_module, "Remote", FakeRemote)
    remote_run()
    assert captured["healthcheck_url"] == "https://hc.example/ping"
    assert callable(captured["states"])


async def test_network_errors_are_retried(monkeypatch) -> None:
    import marketbot.telegram as telegram_module

    async def no_sleep(_seconds) -> None:
        return None

    monkeypatch.setattr(telegram_module.asyncio, "sleep", no_sleep)
    calls = []

    def flaky(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        if len(calls) == 1:
            raise httpx.ReadError("connection closed")
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 7}})

    api = TelegramAPI(TOKEN, transport=httpx.MockTransport(flaky))
    assert (await api.send(OWNER, "result"))["message_id"] == 7
    assert len(calls) == 2


def test_access_loss_message_hints_at_the_ip() -> None:
    from marketbot.telegram import describe

    text = describe(
        "account_access_lost",
        {"error": "HTTP 401, code -2015: Invalid API-key, IP, or permissions"},
        datetime(2026, 10, 22, 13, 0, tzinfo=UTC),
    )
    assert "API ACCESS LOST" in text and "add the new IP" in text


async def test_daily_summary_is_sent_once_after_its_time(rig: Rig) -> None:
    morning = datetime(2026, 10, 22, 8, 59).astimezone()
    rig.remote.clock = lambda: morning
    await rig.remote.maybe_send_summary()
    assert rig.ran == []
    rig.remote.clock = lambda: morning + timedelta(minutes=2)
    await rig.remote.maybe_send_summary()
    await rig.remote.maybe_send_summary()
    assert rig.ran == ["mb summary"]
    rig.remote.clock = lambda: morning + timedelta(days=1, minutes=2)
    await rig.remote.maybe_send_summary()
    assert rig.ran == ["mb summary", "mb summary"]


async def test_monday_summary_adds_the_out_of_time_check(rig: Rig) -> None:
    monday = datetime(2026, 10, 26, 9, 5).astimezone()
    rig.remote.clock = lambda: monday
    await rig.remote.maybe_send_summary()
    assert rig.ran == ["mb summary", "mb forward"]


async def test_plain_commands_run_like_mb_commands(rig: Rig) -> None:
    await rig.message("/status demo")
    await rig.message("/report --hours 48")
    await rig.message("/strategies@SomeBot demo show")
    await rig.message("/risk_scale 0.3")
    assert rig.ran == [
        "mb --env demo status",
        "mb report --hours 48",
        "mb --env demo strategies show",
        "mb risk-scale 0.3",
    ]


async def test_plain_dangerous_commands_still_ask_twice(rig: Rig) -> None:
    await rig.message("/flatten demo")
    assert rig.ran == []
    assert rig.telegram.texts()[-1].startswith("Confirm: /flatten demo")


def buttons_of(body: dict) -> list[str]:
    return [b["callback_data"] for row in body["reply_markup"]["inline_keyboard"] for b in row]


async def test_a_bare_command_opens_its_menu(rig: Rig) -> None:
    await rig.message("/session")
    assert rig.telegram.texts()[-1].endswith("Which bot?")
    assert rig.telegram.buttons() == ["m:session:live", "m:session:demo"]
    await rig.tap("m:session:demo")
    assert rig.telegram.edited[-1]["text"].endswith("Start or stop the session?")
    assert buttons_of(rig.telegram.edited[-1]) == ["m:session:demo:start", "m:session:demo:stop"]
    await rig.tap("m:session:demo:start")
    assert rig.ran == ["mb --env demo session start"]


async def test_a_menu_ending_in_a_dangerous_command_still_asks_twice(rig: Rig) -> None:
    await rig.message("/flatten")
    await rig.tap("m:flatten:live")
    assert rig.ran == []
    assert rig.telegram.texts()[-1].startswith("Confirm: /flatten")


async def test_menu_answers_are_checked(rig: Rig) -> None:
    await rig.tap("m:session:demo:explode")
    await rig.tap("m:keygen:live")
    assert rig.ran == []
    assert all(e["text"].startswith("Menu closed") for e in rig.telegram.edited[-2:])


async def test_close_menu_lists_open_positions(rig: Rig) -> None:
    rig.remote.states = lambda: [BotState("demo", True, True, ["SOLUSDT"])]
    await rig.message("/close demo")
    assert rig.telegram.buttons() == ["m:close:demo:SOLUSDT"]
    await rig.tap("m:close:demo:SOLUSDT")
    assert rig.telegram.texts()[-1].startswith("Confirm: /close demo SOLUSDT")
    await rig.tap("m:close:demo:ETHUSDT")  # not an open position
    assert rig.telegram.edited[-1]["text"].startswith("Menu closed")


async def test_strategy_chooser_toggles_and_saves(rig: Rig) -> None:
    from marketbot.strategy import presets

    names = presets.names()
    await rig.message("/strategies demo")
    await rig.tap("m:strategies:demo:choose")
    assert len(buttons_of(rig.telegram.edited[-1])) == len(names) + 1
    first = names.index("delisting-short-4h")
    await rig.tap(f"s:demo:0:{first}")
    assert f"s:demo:{1 << first:x}:ok" in buttons_of(rig.telegram.edited[-1])
    await rig.tap(f"s:demo:{1 << first:x}:ok")
    assert rig.ran == ["mb --env demo strategies set delisting-short-4h"]
    assert rig.telegram.buttons() == ["m:restart:demo", "x"]


async def test_help_explains_every_command(rig: Rig) -> None:
    await rig.message("/help")
    assert "/propose SYMBOL" in rig.telegram.texts()[-1]
    await rig.message("/help risk-scale")
    assert "5 USDT minimum" in rig.telegram.texts()[-1]
    await rig.message("/nonsense")
    assert rig.telegram.texts()[-1].startswith("Unknown command")
    assert rig.ran == []


async def test_command_menu_is_published(rig: Rig) -> None:
    await rig.remote.publish_commands()
    assert rig.telegram.calls[-1] == "setMyCommands"
