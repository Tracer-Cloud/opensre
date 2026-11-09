"""Regime filters and band inputs, computed causally (value at bar t uses bars <= t only).

They are computed once per timeframe in pandas and streamed into csp next to the price.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from desk import config


def efficiency_ratio(close: pd.Series, window: int = config.ER_WINDOW) -> pd.Series:
    """Kaufman efficiency ratio: |net move| / sum of |bar moves| over ``window`` bars.

    1 = price went in a straight line (trend), 0 = it went nowhere (chop).
    """
    path = close.diff().abs().rolling(window).sum()
    return (close.diff(window).abs() / path).where(path > 0, 0.0)


def _wilder(x: pd.Series, window: int) -> pd.Series:
    return x.ewm(alpha=1.0 / window, adjust=False, min_periods=window).mean()


def true_range(bars: pd.DataFrame) -> pd.Series:
    prev_close = bars["close"].shift(1)
    return pd.concat(
        [
            bars["high"] - bars["low"],
            (bars["high"] - prev_close).abs(),
            (bars["low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)


def atr(bars: pd.DataFrame, window: int = config.ATR_WINDOW) -> pd.Series:
    return _wilder(true_range(bars), window)


def adx(bars: pd.DataFrame, window: int = config.ADX_WINDOW) -> pd.Series:
    """Wilder's Average Directional Index (trend strength, direction-agnostic)."""
    up = bars["high"].diff()
    down = -bars["low"].diff()
    plus_dm = pd.Series(np.where((up > down) & (up > 0), up, 0.0), index=bars.index)
    minus_dm = pd.Series(np.where((down > up) & (down > 0), down, 0.0), index=bars.index)
    tr = _wilder(true_range(bars), window)
    plus_di = 100 * _wilder(plus_dm, window) / tr
    minus_di = 100 * _wilder(minus_dm, window) / tr
    total = plus_di + minus_di
    dx = (100 * (plus_di - minus_di).abs() / total).where(total > 0, 0.0)
    return _wilder(dx, window)


def regime_inputs(bars: pd.DataFrame) -> pd.DataFrame:
    """Everything the filtered strategies stream besides price. NaN during warm-up."""
    return pd.DataFrame(
        {
            "er": efficiency_ratio(bars["close"]),
            "adx": adx(bars),
            "atr": atr(bars),
        },
        index=bars.index,
    )


def bollinger_inputs(close: pd.Series, windows: tuple[int, ...]) -> dict[int, pd.DataFrame]:
    """Rolling mean and population std per window; bands are mean +- k * std."""
    return {
        w: pd.DataFrame({"mid": close.rolling(w).mean(), "sd": close.rolling(w).std(ddof=0)})
        for w in windows
    }
