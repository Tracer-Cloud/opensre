"""Robust construction: trade the whole grid at once instead of picking one combo.

* **Ensemble** -- exposure = average position of every combo in a variant's grid (netted into
  one book), so there is no parameter choice left to overfit.
* **Volatility targeting** -- scale exposure by ``VOL_TARGET / realised vol`` (30 days,
  capped at 1x, re-set once a day). Applied to buy & hold too, so the benchmark is fair.

Fees are charged on the change of the *net* exposure: ``fee_bps x |exposure_t - exposure_t-1|``.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from desk import config
from desk.strategy import ComboRun


def ensemble_exposure(
    runs: dict[tuple[float, float], ComboRun], index: pd.DatetimeIndex
) -> pd.Series:
    """Average position over all combos, in [-1, 1]."""
    total = np.zeros(len(index))
    for run in runs.values():
        total += run.positions.reindex(index, fill_value=0).to_numpy(dtype=float)
    return pd.Series(total / len(runs), index=index, name="exposure")


def vol_weight(close: pd.Series, bar_minutes: int) -> pd.Series:
    """Causal volatility-target weight, changed only on the first bar of each UTC day."""
    bars_per_day = 24 * 60 // bar_minutes
    window = config.VOL_WINDOW_DAYS * bars_per_day
    realised = close.pct_change().rolling(window).std() * config.annualization(bar_minutes)
    weight = (config.VOL_TARGET / realised).clip(upper=config.VOL_MAX_WEIGHT)
    day = pd.Series(close.index.normalize(), index=close.index)
    is_first = day.ne(day.shift(1))
    return weight.where(is_first).ffill().fillna(0.0).rename("weight")


def returns_from_exposure(close: pd.Series, exposure: pd.Series, fee_bps: float) -> pd.Series:
    """Per-bar net return: previous exposure x this bar's move - fee on the exposure change."""
    e = exposure.reindex(close.index).fillna(0.0)
    turnover = e.diff().abs().fillna(e.abs())
    return (
        e.shift(1, fill_value=0.0) * close.pct_change().fillna(0.0) - fee_bps * 1e-4 * turnover
    ).rename("ret")
