"""Turning decided exposures into net returns, under three execution assumptions.

The strategy decides an exposure ``d_t`` at bar ``t``'s close. What it earns depends on when
that decision can actually be filled:

* ``close``      -- at bar ``t``'s close (idealised: the price that produced the signal).
* ``next_open``  -- at bar ``t+1``'s open (what a live bot can do). The old exposure still
  earns the gap from ``close_t`` to ``open_t+1``; the new one earns ``open_t+1 -> close_t+1``.
* ``next_close`` -- a full bar late (stress test for latency / missed bars).

Costs are ``cost_bps x |change of exposure|``, charged on the bar the fill happens.
"""

from __future__ import annotations

import pandas as pd

from desk.strategy import ComboRun


def net_returns(bars: pd.DataFrame, exposure: pd.Series, cost_bps: float, mode: str) -> pd.Series:
    close = bars["close"]
    d = exposure.reindex(close.index).fillna(0.0).astype(float)
    turnover = d.diff().abs().fillna(d.abs())
    cost = cost_bps * 1e-4
    if mode == "close":
        r = d.shift(1, fill_value=0.0) * close.pct_change().fillna(0.0) - cost * turnover
    elif mode == "next_open":
        gap = (bars["open"] / close.shift(1) - 1.0).fillna(0.0)
        intrabar = (close / bars["open"] - 1.0).fillna(0.0)
        held_gap = d.shift(2, fill_value=0.0)
        held_bar = d.shift(1, fill_value=0.0)
        r = (1 + held_gap * gap) * (1 + held_bar * intrabar) - 1.0
        r = r - cost * turnover.shift(1, fill_value=0.0)
    elif mode == "next_close":
        r = d.shift(2, fill_value=0.0) * close.pct_change().fillna(0.0)
        r = r - cost * turnover.shift(1, fill_value=0.0)
    else:
        raise ValueError(f"unknown execution mode {mode!r}")
    return r.rename("ret")


def reprice(
    runs: dict[tuple[float, float], ComboRun],
    bars: pd.DataFrame,
    cost_bps: float,
    mode: str,
) -> dict[tuple[float, float], ComboRun]:
    """The same decisions, re-priced under another cost and execution assumption."""
    out = {}
    for key, run in runs.items():
        fee = (run.fills["position"] - run.fills["prev_position"]).abs() * cost_bps * 1e-4
        out[key] = ComboRun(
            params=run.params,
            returns=net_returns(bars, run.positions, cost_bps, mode),
            positions=run.positions,
            fills=run.fills.assign(fee=fee.astype(float)),
        )
    return out
