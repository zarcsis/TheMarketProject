from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal

from marketbot.risk.config import RiskConfig
from marketbot.risk.project import ProjectState, check_equity, close_window, risk_multiplier
from marketbot.risk.window import HaltReason, Mode, open_window

CONFIG = RiskConfig()
D = Decimal
WINDOW = open_window(1, D(100), datetime(2026, 10, 22, tzinfo=UTC))
DEFENSE_WINDOW = replace(WINDOW, mode=Mode.HALTED, halt_reason=HaltReason.LOSS_LINE)


def test_pause_below_threshold_is_sticky() -> None:
    project = ProjectState(starting_equity=D(123))
    assert not check_equity(project, D("86.10"), CONFIG).paused
    paused = check_equity(project, D("86.09"), CONFIG)
    assert paused.paused
    assert check_equity(paused, D(200), CONFIG).paused
    assert risk_multiplier(paused, CONFIG) == 0


def test_defense_streak_reduces_risk_for_three_windows() -> None:
    project = ProjectState(starting_equity=D(100))
    for _ in range(3):
        project = close_window(project, DEFENSE_WINDOW, CONFIG)
    assert project.reduced_windows_left == 3
    assert risk_multiplier(project, CONFIG) == D("0.5")
    for _ in range(3):
        project = close_window(project, WINDOW, CONFIG)
    assert risk_multiplier(project, CONFIG) == 1


def test_non_defense_window_resets_the_streak() -> None:
    project = ProjectState(starting_equity=D(100))
    project = close_window(project, DEFENSE_WINDOW, CONFIG)
    project = close_window(project, DEFENSE_WINDOW, CONFIG)
    project = close_window(project, WINDOW, CONFIG)
    assert project.defense_streak == 0
    project = close_window(project, DEFENSE_WINDOW, CONFIG)
    assert project.reduced_windows_left == 0
