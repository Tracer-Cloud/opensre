"""Indicators computed with gs-quant's timeseries library (plus a volume profile).

gs-quant has no volume-profile function, so that one is a plain volume-by-price histogram.
"""

from __future__ import annotations

import warnings

import numpy as np
import pandas as pd

from desk import config

with warnings.catch_warnings():
    warnings.simplefilter("ignore", SyntaxWarning)  # gs-quant docstrings carry invalid escapes
    from gs_quant.timeseries import (
        bollinger_bands,
        exponential_moving_average,
        relative_strength_index,
    )


def span_to_beta(span: int) -> float:
    """gs-quant's EMA weight on the previous value for a pandas-style ``span`` in bars.

    gs-quant computes ``x.ewm(alpha=1 - beta, adjust=False)``; span ``n`` means ``alpha=2/(n+1)``.
    """
    return 1.0 - 2.0 / (span + 1.0)


def ema(close: pd.Series, span: int) -> pd.Series:
    return exponential_moving_average(close, beta=span_to_beta(span))


def indicator_frame(bars: pd.DataFrame, fast: int, slow: int) -> pd.DataFrame:
    """EMA(fast), EMA(slow), Bollinger bands and RSI aligned on the bar index."""
    close = bars["close"]
    bands = bollinger_bands(close, w=config.BOLLINGER_WINDOW, k=config.BOLLINGER_K)
    rsi = relative_strength_index(close, w=config.RSI_WINDOW)
    out = pd.DataFrame(
        {
            "close": close,
            f"ema_{fast}": ema(close, fast),
            f"ema_{slow}": ema(close, slow),
            "bb_mid": close.rolling(config.BOLLINGER_WINDOW).mean(),
            "rsi": rsi,
        }
    )
    out["bb_lower"] = bands.iloc[:, 0]
    out["bb_upper"] = bands.iloc[:, 1]
    return out


def volume_profile(bars: pd.DataFrame, bins: int = config.VOLUME_PROFILE_BINS) -> pd.DataFrame:
    """Traded volume per price bucket, using each bar's typical price (H+L+C)/3.

    Also flags the point of control (busiest bucket) and the 70% value area around it.
    """
    typical = (bars["high"] + bars["low"] + bars["close"]) / 3.0
    hist, edges = np.histogram(typical, bins=bins, weights=bars["volume"])
    profile = pd.DataFrame(
        {"price_low": edges[:-1], "price_high": edges[1:], "volume": hist},
    )
    profile["price_mid"] = (profile["price_low"] + profile["price_high"]) / 2.0
    poc = int(profile["volume"].idxmax())
    profile["poc"] = profile.index == poc

    # Grow the value area outward from the POC, always taking the heavier neighbour.
    in_area = {poc}
    lo, hi, total = poc, poc, profile["volume"].sum()
    covered = profile["volume"].iloc[poc]
    while covered < 0.7 * total and (lo > 0 or hi < bins - 1):
        below = profile["volume"].iloc[lo - 1] if lo > 0 else -1.0
        above = profile["volume"].iloc[hi + 1] if hi < bins - 1 else -1.0
        if above >= below:
            hi += 1
            covered += above
            in_area.add(hi)
        else:
            lo -= 1
            covered += below
            in_area.add(lo)
    profile["value_area"] = profile.index.isin(in_area)
    return profile
