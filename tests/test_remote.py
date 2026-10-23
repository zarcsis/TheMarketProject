from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from marketbot import remote
from marketbot.remote import RemoteCommandError, parse


def test_parse_allowed_commands() -> None:
    command = parse("/mb --env demo risk-scale 0.5")
    assert (command.env, command.name, command.args) == (
        "demo",
        "risk-scale",
        ("--env", "demo", "risk-scale", "0.5"),
    )
    assert command.text() == "mb --env demo risk-scale 0.5"
    assert not command.dangerous
    assert parse('mb propose SOLUSDT long --stop 98 --why "test"').name == "propose"


@pytest.mark.parametrize(
    ("text", "dangerous", "blocking"),
    [
        ("flatten", True, False),
        ("close SOLUSDT", True, False),
        ("transfer 50 to-futures", True, False),
        ("session stop", True, False),
        ("session start", False, True),
        ("status", False, False),
        ("ps", False, False),
    ],
)
def test_dangerous_and_blocking(text, dangerous, blocking) -> None:
    command = parse(text)
    assert command.dangerous is dangerous
    assert command.blocking is blocking


@pytest.mark.parametrize(
    "text",
    ["keygen", "research strategies", "bot", "data download", "", "--env prod status", "session"],
)
def test_disallowed_commands(text) -> None:
    with pytest.raises(RemoteCommandError):
        parse(text)


def test_shell_metacharacters_are_plain_arguments() -> None:
    # Arguments go to the mb program as a list, never through a shell.
    with pytest.raises(RemoteCommandError):
        parse("status; rm -rf /")  # "status;" is not a command
    assert parse("status && echo x").args == ("status", "&&", "echo", "x")


def test_proposals_expire(tmp_path: Path) -> None:
    db = tmp_path / "remote.db"
    proposal_id = remote.propose("pause", "test", timedelta(minutes=5), path=db)
    [command] = remote.store(db).pending()
    assert command.id == proposal_id
    assert command.payload["command"] == "mb pause"
    assert not remote.expired(command.payload)
    assert remote.expired(command.payload, datetime.now(UTC) + timedelta(minutes=6))
    with pytest.raises(RemoteCommandError):
        remote.propose("keygen", "no", timedelta(minutes=5), path=db)


def test_every_allowed_command_is_documented() -> None:
    import re

    assert set(remote.ALLOWED) <= set(remote.DETAILS)
    assert set(remote.ALLOWED) | {"help"} == set(remote.MENU)
    for name, description in remote.MENU.items():
        assert re.fullmatch(r"[a-z0-9_]{1,32}", remote.telegram_name(name))
        assert 3 <= len(description) <= 256
    assert len(remote.HELP) < 4000  # one Telegram message


def test_telegram_commands() -> None:
    assert remote.command_text("/status") == "mb status"
    assert remote.command_text("/status demo") == "mb --env demo status"
    assert remote.command_text("/risk_scale 0.3") == "mb risk-scale 0.3"
    assert remote.command_text("/risk-scale demo 0.5") == "mb --env demo risk-scale 0.5"
    assert remote.command_text("/strategies") == "mb strategies show"
    assert remote.command_text("/session start demo") == "mb --env demo session start"
    assert remote.command_text("/session demo start") == "mb --env demo session start"
    assert remote.command_text("/strategies demo") == "mb --env demo strategies show"
    assert remote.command_text("/events --limit 5") == "mb events --limit 5"
    assert remote.command_text("/auto@SomeBot on") == "mb auto on"
    assert remote.command_text("/mb --env demo status") == "mb --env demo status"
    assert remote.command_text("/keygen") is None
    assert remote.command_text("hello") is None


def test_commands_are_shown_as_typed() -> None:
    assert parse("mb --env demo risk-scale 0.3").typed() == "/risk_scale demo 0.3"
    assert parse("mb close SOLUSDT").typed() == "/close SOLUSDT"
    assert parse("mb session stop").typed() == "/session stop"


def test_menus_check_their_answers() -> None:
    from marketbot import remote_menus as menus

    assert menus.next_step("session", [], {}) is menus.ENV
    assert menus.next_step("session", ["demo"], {}).options == (
        ("Start", "start"),
        ("Stop", "stop"),
    )
    assert menus.next_step("session", ["demo", "stop"], {}) is None
    assert menus.command("session", ["demo", "stop"]) == "mb --env demo session stop"
    assert menus.command("report", ["live", "72"]) == "mb report --hours 72"
    assert menus.command("transfer", ["to-spot", "25"]) == "mb transfer 25 to-spot"
    close = menus.next_step("close", ["live"], {"live": ["SOLUSDT"]})
    assert close.options == (("SOLUSDT", "SOLUSDT"),)
    for bad in (["live", "SOLUSDT; rm"], ["paper"], ["live", "start", "x"]):
        with pytest.raises(menus.MenuError):
            menus.next_step("close" if len(bad) == 2 else "session", bad, {"live": ["SOLUSDT"]})
    for name in menus.STEPS:  # every menu command is allowed, and its data fits Telegram
        assert name in remote.ALLOWED
        menus.encode(name, ["demo", "x" * 20])


def test_strategy_chooser_masks() -> None:
    from marketbot import remote_menus as menus

    presets = ["a", "b", "c"]
    assert menus.mask_of("a,c", presets) == 0b101
    assert menus.chosen(0b101, presets) == ["a", "c"]
    assert menus.strategy_answer("s:demo:5:1", presets) == ("demo", 0b111, False)
    assert menus.strategy_answer("s:live:5:ok", presets) == ("live", 0b101, True)
    for bad in ("s:paper:5:1", "s:demo:ff:1", "s:demo:5:9", "s:demo:zz:1"):
        with pytest.raises(menus.MenuError):
            menus.strategy_answer(bad, presets)
