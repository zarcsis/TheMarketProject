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
