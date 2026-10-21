"""Project-level circuit breakers that span windows (plan section 4.5)."""

from dataclasses import dataclass, replace
from decimal import Decimal

from marketbot.risk.config import RiskConfig
from marketbot.risk.window import HaltReason, WindowState


@dataclass(frozen=True)
class ProjectState:
    starting_equity: Decimal
    defense_streak: int = 0
    reduced_windows_left: int = 0
    paused: bool = False


def risk_multiplier(project: ProjectState, config: RiskConfig) -> Decimal:
    """Factor on risk per trade; 0 while the project is paused."""
    if project.paused:
        return Decimal(0)
    if project.reduced_windows_left:
        return config.project.reduced_risk_per_trade / config.trade.risk_per_trade
    return Decimal(1)


def check_equity(project: ProjectState, equity: Decimal, config: RiskConfig) -> ProjectState:
    """Pause the project when equity falls below the threshold; only a person resumes it."""
    if equity < project.starting_equity * config.project.pause_below:
        return replace(project, paused=True)
    return project


def close_window(project: ProjectState, closed: WindowState, config: RiskConfig) -> ProjectState:
    limits = config.project
    if closed.halt_reason is HaltReason.LOSS_LINE:
        streak = project.defense_streak + 1
        if streak >= limits.defense_streak:
            return replace(project, defense_streak=0, reduced_windows_left=limits.reduced_windows)
    else:
        streak = 0
    return replace(
        project,
        defense_streak=streak,
        reduced_windows_left=max(project.reduced_windows_left - 1, 0),
    )
