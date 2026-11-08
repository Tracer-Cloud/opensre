"""The strategy variants compared in the multi-year study, and how to run one."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import pandas as pd

from desk import config, filters, mean_reversion, strategy, sweep
from desk.strategy import ComboRun

Regime = Literal["none", "er_trend", "adx_trend", "er_chop"]


@dataclass(frozen=True)
class Variant:
    name: str
    family: Literal["trend", "mean_reversion"]
    regime: Regime
    flat_capable: bool
    min_gap_atr: float = 0.0
    cooldown: int = 0
    description: str = ""

    @property
    def axes(self) -> tuple[str, str]:
        return ("fast", "slow") if self.family == "trend" else ("window", "width")


VARIANTS: tuple[Variant, ...] = (
    Variant(
        "always_in", "trend", "none", False, description="original: EMA cross, always long or short"
    ),
    Variant(
        "gap_cooldown",
        "trend",
        "none",
        True,
        config.MIN_GAP_ATR,
        config.COOLDOWN_BARS,
        "EMA direction, enter only with gap >= 0.25 ATR, 4-bar cooldown, flat otherwise",
    ),
    Variant(
        "flat_er",
        "trend",
        "er_trend",
        True,
        description="EMA direction only while ER(20) >= 0.30, else flat",
    ),
    Variant(
        "flat_adx",
        "trend",
        "adx_trend",
        True,
        description="EMA direction only while ADX(14) >= 25, else flat",
    ),
    Variant(
        "flat_adx_gap_cd",
        "trend",
        "adx_trend",
        True,
        config.MIN_GAP_ATR,
        config.COOLDOWN_BARS,
        "ADX >= 25 + gap >= 0.25 ATR + 4-bar cooldown, else flat",
    ),
    Variant(
        "mr_always",
        "mean_reversion",
        "none",
        True,
        description="fade Bollinger touches, exit at middle band",
    ),
    Variant(
        "mr_chop",
        "mean_reversion",
        "er_chop",
        True,
        description="fade Bollinger touches only while ER(20) < 0.30; flat when a trend starts",
    ),
)
BY_NAME = {v.name: v for v in VARIANTS}


def regime_mask(regime: Regime, inputs: pd.DataFrame) -> pd.Series:
    """Boolean regime per bar; False while the filter is still warming up (NaN)."""
    if regime == "none":
        return pd.Series(True, index=inputs.index)
    if regime == "er_trend":
        return inputs["er"] >= config.ER_TREND
    if regime == "adx_trend":
        return inputs["adx"] >= config.ADX_TREND
    return inputs["er"] < config.ER_TREND  # er_chop (NaN compares False -> no trading)


def combos_for(variant: Variant) -> list[tuple[float, float]]:
    return list(sweep.grid()) if variant.family == "trend" else list(mean_reversion.grid())


def run_variant(
    bars: pd.DataFrame, variant: Variant, inputs: pd.DataFrame | None = None
) -> dict[tuple[float, float], ComboRun]:
    """Run every combo of ``variant``'s grid over ``bars``."""
    inputs = inputs if inputs is not None else filters.regime_inputs(bars)
    combos = combos_for(variant)
    if variant.family == "mean_reversion":
        return mean_reversion.run(bars, combos, chop_ok=regime_mask(variant.regime, inputs))
    if not variant.flat_capable:
        return strategy.run(bars, combos)
    return strategy.run(
        bars,
        combos,
        trend_ok=regime_mask(variant.regime, inputs),
        atr=inputs["atr"],
        min_gap_atr=variant.min_gap_atr,
        cooldown=variant.cooldown,
    )
