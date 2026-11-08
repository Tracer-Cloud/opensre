"""Parameter sweep, Sharpe ranking, per-window stability, plateau/peak labels, walk-forward."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from desk import config
from desk.strategy import ComboRun

EMA_AXES = ("fast", "slow")


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


def _sharpe_np(r: np.ndarray, ann: float) -> float:
    if len(r) < 2:
        return 0.0
    sd = r.std(ddof=1)
    return 0.0 if not np.isfinite(sd) or sd == 0.0 else float(r.mean() / sd * ann)


def sharpe(returns: pd.Series, ann: float = config.ANNUALIZATION) -> float:
    return _sharpe_np(returns.to_numpy(dtype=float), ann)


def max_drawdown(returns: pd.Series) -> float:
    equity = (1.0 + returns).cumprod()
    return float((equity / equity.cummax() - 1.0).min())


def window_slices(n_bars: int, n: int) -> list[slice]:
    edges = np.linspace(0, n_bars, n + 1).round().astype(int)
    return [slice(int(a), int(b)) for a, b in zip(edges[:-1], edges[1:], strict=True)]


def window_bounds(index: pd.DatetimeIndex, n: int = config.N_WINDOWS) -> list[pd.DatetimeIndex]:
    """Split the bar index into ``n`` contiguous, equal-length windows."""
    return [index[s] for s in window_slices(len(index), n)]


def summarise(
    runs: dict[tuple[float, float], ComboRun],
    index: pd.DatetimeIndex,
    n_windows: int = config.N_WINDOWS,
    ann: float = config.ANNUALIZATION,
    axes: tuple[str, str] = EMA_AXES,
) -> pd.DataFrame:
    """One row per combo: full-period stats plus return and Sharpe in each window."""
    slices = window_slices(len(index), n_windows)
    rows = []
    for (a, b), run in runs.items():
        r = run.returns.reindex(index, fill_value=0.0).to_numpy(dtype=float)
        pos = run.positions.reindex(index, fill_value=0).to_numpy()
        row: dict[str, float | int] = {
            axes[0]: a,
            axes[1]: b,
            "sharpe": _sharpe_np(r, ann),
            "total_return": float(np.prod(1.0 + r) - 1.0),
            "max_drawdown": max_drawdown(pd.Series(r)),
            "trades": len(run.fills),
            "fees_paid": float(run.fills["fee"].sum()) if len(run.fills) else 0.0,
            "exposure": float(np.mean(pos != 0)),
        }
        for i, s in enumerate(slices, start=1):
            row[f"w{i}_return"] = float(np.prod(1.0 + r[s]) - 1.0)
            row[f"w{i}_sharpe"] = _sharpe_np(r[s], ann)
        rows.append(row)
    table = pd.DataFrame(rows).sort_values("sharpe", ascending=False).reset_index(drop=True)
    table.insert(0, "rank", np.arange(1, len(table) + 1))
    window_cols = [f"w{i}_return" for i in range(1, n_windows + 1)]
    table["positive_windows"] = (table[window_cols] > 0).sum(axis=1)
    return table


def label_plateaus(
    table: pd.DataFrame,
    axes: tuple[str, str] = EMA_AXES,
    min_positive_windows: int = config.PLATEAU_MIN_POSITIVE_WINDOWS,
    n_windows: int = config.N_WINDOWS,
) -> pd.DataFrame:
    """Label each combo as a *plateau* (robust) or a *peak* (isolated/fragile).

    A plateau needs both:
      * its grid neighbours (+-1 step on either axis, diagonals included) keep a median
        Sharpe of at least ``PLATEAU_NEIGHBOUR_RATIO`` x its own (and positive) -> the good
        result is not a knife-edge parameter choice;
      * it is profitable in at least ``min_positive_windows`` windows -> not one lucky stretch.
    Anything else with Sharpe > 0 is a peak; Sharpe <= 0 is a "loser".
    """
    a_vals = sorted(table[axes[0]].unique())
    b_vals = sorted(table[axes[1]].unique())
    a_pos = {v: i for i, v in enumerate(a_vals)}
    b_pos = {v: i for i, v in enumerate(b_vals)}
    by_cell = {
        (a_pos[a], b_pos[b]): float(sh)
        for a, b, sh in table[[axes[0], axes[1], "sharpe"]].itertuples(index=False)
    }
    neigh_median, neigh_count, labels, reasons = [], [], [], []
    for a, b, sh, pw in table[[axes[0], axes[1], "sharpe", "positive_windows"]].itertuples(
        index=False
    ):
        i, j = a_pos[a], b_pos[b]
        neighbours = [
            by_cell[(i + di, j + dj)]
            for di in (-1, 0, 1)
            for dj in (-1, 0, 1)
            if (di, dj) != (0, 0) and (i + di, j + dj) in by_cell
        ]
        med = float(np.median(neighbours)) if neighbours else float("nan")
        neigh_median.append(med)
        neigh_count.append(len(neighbours))
        if sh <= 0:
            labels.append("loser")
            reasons.append("Sharpe <= 0")
            continue
        stable_grid = med > 0 and med >= config.PLATEAU_NEIGHBOUR_RATIO * sh
        stable_time = pw >= min_positive_windows
        if stable_grid and stable_time:
            labels.append("plateau")
            reasons.append(
                f"neighbours hold {med / sh:.0%} of Sharpe; {pw}/{n_windows} windows positive"
            )
        else:
            why = []
            if not stable_grid:
                why.append(f"neighbours median Sharpe {med:.2f} vs own {sh:.2f}")
            if not stable_time:
                why.append(f"only {pw}/{n_windows} windows positive")
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


def walk_forward(
    runs: dict[tuple[float, float], ComboRun],
    index: pd.DatetimeIndex,
    n_windows: int = config.N_WINDOWS,
    ann: float = config.ANNUALIZATION,
) -> WalkForward:
    """Rolling walk-forward: pick the best-Sharpe combo on window k, trade it on window k+1.

    Each strategy runs continuously, so the position entering window k+1 depends only on
    data up to the end of window k; the choice of combo only uses window-k returns.
    """
    slices = window_slices(len(index), n_windows)
    keys = list(runs)
    matrix = np.column_stack(
        [runs[k].returns.reindex(index, fill_value=0.0).to_numpy(dtype=float) for k in keys]
    )
    steps, oos_parts = [], []
    for k in range(n_windows - 1):
        train, test = slices[k], slices[k + 1]
        in_sample = [_sharpe_np(matrix[train, c], ann) for c in range(len(keys))]
        best = int(np.argmax(in_sample))
        test_r = pd.Series(matrix[test, best], index=index[test])
        oos_parts.append(test_r)
        a, b = keys[best]
        steps.append(
            {
                "train": f"W{k + 1}",
                "test": f"W{k + 2}",
                "picked": f"{a:g}/{b:g}",
                "in_sample_sharpe": in_sample[best],
                "oos_sharpe": _sharpe_np(test_r.to_numpy(), ann),
                "oos_return": float((1.0 + test_r).prod() - 1.0),
            }
        )
    return WalkForward(steps=pd.DataFrame(steps), oos_returns=pd.concat(oos_parts))
