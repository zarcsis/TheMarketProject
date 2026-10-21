"""Risk limits loaded from `config/risk.toml`."""

import tomllib
from decimal import Decimal
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

DEFAULT_PATH = Path("config/risk.toml")

Fraction = Decimal


class _Section(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    @field_validator("*", mode="before")
    @classmethod
    def _exact_decimal(cls, value: Any) -> Any:
        # TOML floats are binary; go through text so 0.1 becomes Decimal("0.1").
        return str(value) if isinstance(value, float) else value


class WindowLimits(_Section):
    length_hours: int = Field(24, gt=0)
    target: Fraction = Field(Decimal("0.10"), gt=0)
    loss_line: Fraction = Field(Decimal("0.09"), gt=0, lt=1)
    caution_at: Fraction = Field(Decimal("0.05"), gt=0, lt=1)
    caution_recover_at: Fraction = Field(Decimal("0.03"), ge=0, lt=1)
    lock_floor_min: Fraction = Field(Decimal("0.09"), ge=0)
    lock_floor_share: Fraction = Field(Decimal("0.70"), gt=0, le=1)
    loss_line_buffer: Fraction = Field(Decimal("0.005"), ge=0)


class TradeLimits(_Section):
    risk_per_trade: Fraction = Field(Decimal("0.03"), gt=0, lt=1)
    caution_size: Fraction = Field(Decimal("0.5"), gt=0, le=1)
    protect_size: Fraction = Field(Decimal("0.5"), gt=0, le=1)
    max_positions: int = Field(3, ge=1)
    caution_max_positions: int = Field(1, ge=1)
    max_same_direction_per_bucket: int = Field(2, ge=1)
    max_trades_per_window: int = Field(12, ge=1)


class LeverageLimits(_Section):
    max_effective: Decimal = Field(Decimal(5), gt=0)
    max_exchange: int = Field(10, ge=1, le=125)
    liquidation_distance_multiple: Decimal = Field(Decimal(2), ge=1)


class Costs(_Section):
    taker_fee: Fraction = Field(Decimal("0.0005"), ge=0)
    maker_fee: Fraction = Field(Decimal("0.0002"), ge=0)
    stop_slippage: Fraction = Field(Decimal("0.001"), ge=0)


class ProjectLimits(_Section):
    pause_below: Fraction = Field(Decimal("0.70"), gt=0, lt=1)
    defense_streak: int = Field(3, ge=1)
    reduced_risk_per_trade: Fraction = Field(Decimal("0.015"), gt=0)
    reduced_windows: int = Field(3, ge=1)


class RiskConfig(_Section):
    window: WindowLimits = WindowLimits()
    trade: TradeLimits = TradeLimits()
    leverage: LeverageLimits = LeverageLimits()
    costs: Costs = Costs()
    project: ProjectLimits = ProjectLimits()


def load_risk_config(path: Path = DEFAULT_PATH) -> RiskConfig:
    with path.open("rb") as file:
        return RiskConfig.model_validate(tomllib.load(file))
