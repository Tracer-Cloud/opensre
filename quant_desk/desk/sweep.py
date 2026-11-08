"""Parameter sweep, Sharpe ranking, per-window stability, plateau/peak labels, walk-forward."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from desk import config
from desk.strategy import ComboRun


def grid() -> list[tuple[int, int]]:
    """fast 4..24 x slow 12..96 with slow >= 1.5 * fast (212 combos)."""
    combos = [
        (f, s)
        for f in config.FAST_SPANS
        for s in config.SLOW_SPANS
        if s >= config.MIN_SLOW_OVER_FAST * f
    ]
    if len(combos) != config.EXPECTED_COMBOS:
        raise ValueError(f"grid has {len(combos)} combos, expected {config.EXPECTED_COMBOS}")
    return combos


def sharpe(returns: pd.Series) -> float:
    sd = returns.std(ddof=1)
    if not np.isfinite(sd) or sd == 0.0:
        return 0.0
    return float(returns.mean() / sd * config.ANNUALIZATION)


def max_drawdown(returns: pd.Series) -> float:
    equity = (1.0 + returns).cumprod()
    return float((equity / equity.cummax() - 1.0).min())


def window_bounds(index: pd.DatetimeIndex, n: int = config.N_WINDOWS) -> list[pd.DatetimeIndex]:
    """Split the bar index into ``n`` contiguous, equal-length windows."""
    return [index[chunk] for chunk in np.array_split(np.arange(len(index)), n)]


def summarise(runs: dict[tuple[int, int], ComboRun], index: pd.DatetimeIndex) -> pd.DataFrame:
    """One row per combo: full-period stats plus return and Sharpe in each window."""
    windows = window_bounds(index)
    rows = []
    for (fast, slow), run in runs.items():
        r = run.returns.reindex(index, fill_value=0.0)
        row: dict[str, float | int] = {
            "fast": fast,
            "slow": slow,
            "sharpe": sharpe(r),
            "total_return": float((1.0 + r).prod() - 1.0),
            "max_drawdown": max_drawdown(r),
            "trades": len(run.fills),
            "fees_paid": float(run.fills["fee"].sum()) if len(run.fills) else 0.0,
        }
        for i, w in enumerate(windows, start=1):
            rw = r.loc[w]
            row[f"w{i}_return"] = float((1.0 + rw).prod() - 1.0)
            row[f"w{i}_sharpe"] = sharpe(rw)
        rows.append(row)
    table = pd.DataFrame(rows).sort_values("sharpe", ascending=False).reset_index(drop=True)
    table.insert(0, "rank", np.arange(1, len(table) + 1))
    window_cols = [f"w{i}_return" for i in range(1, len(windows) + 1)]
    table["positive_windows"] = (table[window_cols] > 0).sum(axis=1)
    return table


def label_plateaus(table: pd.DataFrame) -> pd.DataFrame:
    """Label each combo as a *plateau* (robust) or a *peak* (isolated/fragile).

    A plateau needs both:
      * its grid neighbours (+-1 step in fast and/or slow) keep a median Sharpe of at least
        ``PLATEAU_NEIGHBOUR_RATIO`` x its own (and positive) -> the good result is not a
        knife-edge parameter choice;
      * it is profitable in at least ``PLATEAU_MIN_POSITIVE_WINDOWS`` of the windows -> the
        result is not one lucky week.
    Anything else is a peak. Combos with Sharpe <= 0 are labelled "loser".
    """
    by_key = {(int(f), int(s)): float(sh) for f, s, sh in table[["fast", "slow", "sharpe"]].values}
    fast_step = config.FAST_SPANS[1] - config.FAST_SPANS[0]
    slow_step = config.SLOW_SPANS[1] - config.SLOW_SPANS[0]
    neigh_median, neigh_count, labels, reasons = [], [], [], []
    for row in table.itertuples(index=False):
        neighbours = [
            by_key[k]
            for df in (-fast_step, 0, fast_step)
            for ds in (-slow_step, 0, slow_step)
            if (df, ds) != (0, 0) and (k := (row.fast + df, row.slow + ds)) in by_key
        ]
        med = float(np.median(neighbours)) if neighbours else float("nan")
        neigh_median.append(med)
        neigh_count.append(len(neighbours))
        if row.sharpe <= 0:
            labels.append("loser")
            reasons.append("Sharpe <= 0")
            continue
        stable_grid = med > 0 and med >= config.PLATEAU_NEIGHBOUR_RATIO * row.sharpe
        stable_time = row.positive_windows >= config.PLATEAU_MIN_POSITIVE_WINDOWS
        if stable_grid and stable_time:
            labels.append("plateau")
            reasons.append(
                f"neighbours hold {med / row.sharpe:.0%} of Sharpe; "
                f"{row.positive_windows}/{config.N_WINDOWS} windows positive"
            )
        else:
            why = []
            if not stable_grid:
                why.append(f"neighbours median Sharpe {med:.2f} vs own {row.sharpe:.2f}")
            if not stable_time:
                why.append(f"only {row.positive_windows}/{config.N_WINDOWS} windows positive")
            labels.append("peak")
            reasons.append("; ".join(why))
    out = table.copy()
    out["neighbour_median_sharpe"] = neigh_median
    out["neighbours"] = neigh_count
    out["label"] = labels
    out["why"] = reasons
    return out


@dataclass(frozen=True)
class WalkForward:
    steps: pd.DataFrame  # one row per (train window -> test window)
    oos_returns: pd.Series  # stitched out-of-sample returns


def walk_forward(runs: dict[tuple[int, int], ComboRun], index: pd.DatetimeIndex) -> WalkForward:
    """Rolling walk-forward: pick the best-Sharpe combo on window k, trade it on window k+1.

    Each strategy runs continuously, so the position entering window k+1 depends only on
    data up to the end of window k; the choice of combo only uses window-k returns.
    """
    windows = window_bounds(index)
    full = {k: run.returns.reindex(index, fill_value=0.0) for k, run in runs.items()}
    steps, oos_parts = [], []
    for k in range(len(windows) - 1):
        train, test = windows[k], windows[k + 1]
        in_sample = {key: sharpe(r.loc[train]) for key, r in full.items()}
        best = max(in_sample, key=lambda key: in_sample[key])
        test_r = full[best].loc[test]
        oos_parts.append(test_r)
        steps.append(
            {
                "train": f"W{k + 1}",
                "test": f"W{k + 2}",
                "picked": f"{best[0]}/{best[1]}",
                "in_sample_sharpe": in_sample[best],
                "oos_sharpe": sharpe(test_r),
                "oos_return": float((1.0 + test_r).prod() - 1.0),
            }
        )
    return WalkForward(steps=pd.DataFrame(steps), oos_returns=pd.concat(oos_parts))
