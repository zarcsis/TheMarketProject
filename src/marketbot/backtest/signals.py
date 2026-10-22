"""Entry signals on closed bars. Each returns rows (entry_time, direction, stop, tag).

`entry_time` is the open time of the intraday bar to enter on: the first bar after the
signal bar has closed. Stops are prices.
"""

from dataclasses import dataclass

import polars as pl

SIGNAL_COLUMNS = ["entry_time", "direction", "stop", "tag"]


def resample(bars: pl.DataFrame, every: str) -> pl.DataFrame:
    """OHLCV bars on a coarser timeframe, labelled by their open time."""
    return (
        bars.sort("open_time")
        .group_by_dynamic("open_time", every=every, closed="left", label="left")
        .agg(
            pl.col("open").first(),
            pl.col("high").max(),
            pl.col("low").min(),
            pl.col("close").last(),
            pl.col("quote_volume").sum(),
            pl.len().alias("parts"),
        )
    )


def with_indicators(sig: pl.DataFrame, every: str, atr_n: int = 14) -> pl.DataFrame:
    """ATR (simple mean of true range) and the bar's close time."""
    previous_close = pl.col("close").shift(1)
    true_range = pl.max_horizontal(
        pl.col("high") - pl.col("low"),
        (pl.col("high") - previous_close).abs(),
        (pl.col("low") - previous_close).abs(),
    )
    return sig.with_columns(
        atr=true_range.rolling_mean(atr_n),
        close_time=pl.col("open_time").dt.offset_by(every),
    )


def _empty() -> pl.DataFrame:
    return pl.DataFrame(
        schema={
            "entry_time": pl.Datetime("ms", "UTC"),
            "direction": pl.Int8,
            "stop": pl.Float64,
            "tag": pl.String,
        }
    )


@dataclass(frozen=True)
class Donchian:
    """Close beyond the prior n-bar range, with a volume surge; ATR stop."""

    n: int = 24
    volume_mult: float = 1.5
    stop_atr: float = 1.5

    @property
    def tag(self) -> str:
        return f"donchian n={self.n} vol>{self.volume_mult}x stop={self.stop_atr}atr"

    def signals(self, sig: pl.DataFrame) -> pl.DataFrame:
        prior_high = pl.col("high").shift(1).rolling_max(self.n)
        prior_low = pl.col("low").shift(1).rolling_min(self.n)
        prior_volume = pl.col("quote_volume").shift(1).rolling_mean(self.n)
        surge = pl.col("quote_volume") > self.volume_mult * prior_volume
        frame = sig.with_columns(
            direction=pl.when((pl.col("close") > prior_high) & surge)
            .then(1)
            .when((pl.col("close") < prior_low) & surge)
            .then(-1)
            .otherwise(0)
            .cast(pl.Int8)
        ).filter((pl.col("direction") != 0) & pl.col("atr").is_not_null())
        return frame.select(
            entry_time=pl.col("close_time"),
            direction="direction",
            stop=pl.col("close") - pl.col("direction") * self.stop_atr * pl.col("atr"),
            tag=pl.lit(self.tag),
        )


@dataclass(frozen=True)
class Fade:
    """Fade a bar that moved more than k ATR on a volume surge; stop beyond its extreme."""

    k_atr: float = 3.0
    volume_mult: float = 3.0
    stop_pad_atr: float = 0.25

    @property
    def tag(self) -> str:
        return f"fade k={self.k_atr}atr vol>{self.volume_mult}x"

    def signals(self, sig: pl.DataFrame) -> pl.DataFrame:
        prior_atr = pl.col("atr").shift(1)
        prior_volume = pl.col("quote_volume").shift(1).rolling_mean(24)
        move = pl.col("close") - pl.col("open")
        surge = pl.col("quote_volume") > self.volume_mult * prior_volume
        frame = sig.with_columns(
            direction=pl.when((move > self.k_atr * prior_atr) & surge)
            .then(-1)
            .when((move < -self.k_atr * prior_atr) & surge)
            .then(1)
            .otherwise(0)
            .cast(pl.Int8)
        ).filter(pl.col("direction") != 0)
        extreme = pl.when(pl.col("direction") < 0).then(pl.col("high")).otherwise(pl.col("low"))
        return frame.select(
            entry_time=pl.col("close_time"),
            direction="direction",
            stop=extreme - pl.col("direction") * self.stop_pad_atr * pl.col("atr"),
            tag=pl.lit(self.tag),
        )


@dataclass(frozen=True)
class OpeningRange:
    """First close beyond the 00:00 UTC opening range; stop at the range's other side or mid.

    Works on the intraday bars directly; at most one long and one short per day.
    """

    range_hours: int = 2
    stop_at_mid: bool = True
    last_entry_hour: int = 20

    @property
    def tag(self) -> str:
        stop = "mid" if self.stop_at_mid else "far side"
        return f"opening range {self.range_hours}h stop={stop}"

    def signals(self, bars: pl.DataFrame) -> pl.DataFrame:
        hour = pl.col("open_time").dt.hour()
        frame = bars.sort("open_time").with_columns(
            day=pl.col("open_time").dt.date(),
            next_open_time=pl.col("open_time").shift(-1),
        )
        ranges = (
            frame.filter(hour < self.range_hours)
            .group_by("day")
            .agg(range_high=pl.col("high").max(), range_low=pl.col("low").min(), n=pl.len())
        )
        expected = self.range_hours * 12  # 5-minute bars
        frame = frame.join(ranges.filter(pl.col("n") >= expected), on="day").filter(
            (hour >= self.range_hours) & (hour < self.last_entry_hour)
        )
        frame = frame.with_columns(
            direction=pl.when(pl.col("close") > pl.col("range_high"))
            .then(1)
            .when(pl.col("close") < pl.col("range_low"))
            .then(-1)
            .otherwise(0)
            .cast(pl.Int8)
        ).filter(pl.col("direction") != 0)
        first = frame.group_by("day", "direction").agg(pl.all().sort_by("open_time").first())
        mid = (pl.col("range_high") + pl.col("range_low")) / 2
        far = pl.when(pl.col("direction") > 0).then("range_low").otherwise("range_high")
        return first.filter(pl.col("next_open_time").is_not_null()).select(
            entry_time=pl.col("next_open_time"),
            direction="direction",
            stop=mid if self.stop_at_mid else far,
            tag=pl.lit(self.tag),
        )
