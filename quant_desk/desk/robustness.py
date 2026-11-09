"""Robustness checks: each one answers a caveat of the multi-year study.

* :func:`execution_test` -- does the result survive realistic fills and costs?
* :func:`meta_walk_forward` -- pick the *book* (variant x sizing x bar size) from past data only,
  so choosing "the best row" is itself out-of-sample.
* :func:`deflated_sharpe_table` -- discount each book's Sharpe for the number of books tried.
* :func:`sensitivity` -- move every untuned threshold around its default; report the worst case.
"""

from __future__ import annotations

import dataclasses
import multiprocessing as mp
from concurrent.futures import ProcessPoolExecutor

import numpy as np
import pandas as pd
from scipy import stats

from desk import config, ensemble, history, sweep, variants

DAILY_ANN = 365**0.5
EULER_GAMMA = 0.5772156649015329


def execution_test(study: history.Study) -> pd.DataFrame:
    """Every candidate book under each execution mode and both cost levels."""
    return pd.concat(
        [
            history.candidates(study, cost, mode)
            for mode in config.EXECUTION_MODES
            for cost in (config.COST_BPS, config.OPTIMISTIC_COST_BPS)
        ],
        ignore_index=True,
    )


def to_daily(r: pd.Series) -> pd.Series:
    """Compound close-stamped bar returns into UTC calendar days."""
    return (1 + r).resample("1D", label="right", closed="right").prod() - 1


def daily_books(
    study: history.Study, cost_bps: float = config.COST_BPS, mode: str = config.EXECUTION
) -> pd.DataFrame:
    """Daily returns of every book on every bar size, columns ``(timeframe, book, sizing)``."""
    cols = {
        (tf, name, sizing): to_daily(history.book_returns(bars, exposure, cost_bps, mode))
        for tf, bars in study.bars.items()
        for name, sizing, exposure in history.books(study, tf)
    }
    frame = pd.DataFrame(cols).fillna(0.0)
    frame.columns = pd.MultiIndex.from_tuples(frame.columns, names=["timeframe", "book", "sizing"])
    return frame


def _strategy_columns(daily: pd.DataFrame) -> list[tuple[str, str, str]]:
    return [c for c in daily.columns if c[1] != "buy & hold"]


def meta_walk_forward(
    daily: pd.DataFrame, n_windows: int = config.HISTORY_WINDOWS
) -> sweep.WalkForward:
    """On each window pick the best-Sharpe strategy book (any variant, sizing, bar size);
    trade it on the next window. Benchmarks are never eligible."""
    cols = _strategy_columns(daily)
    matrix = daily[cols].to_numpy()
    slices = sweep.window_slices(len(daily), n_windows)
    steps, parts = [], []
    for k in range(n_windows - 1):
        train, test = slices[k], slices[k + 1]
        in_sample = [sweep.sharpe_np(matrix[train, c], DAILY_ANN) for c in range(len(cols))]
        best = int(np.argmax(in_sample))
        oos = pd.Series(matrix[test, best], index=daily.index[test])
        parts.append(oos)
        tf, book, sizing = cols[best]
        steps.append(
            {
                "train": f"W{k + 1}",
                "test": f"W{k + 2}",
                "picked": f"{tf} {book} ({sizing})",
                "in_sample_sharpe": in_sample[best],
                "oos_sharpe": sweep.sharpe_np(oos.to_numpy(), DAILY_ANN),
                "oos_return": float((1 + oos).prod() - 1),
            }
        )
    return sweep.WalkForward(steps=pd.DataFrame(steps), oos_returns=pd.concat(parts))


def deflated_sharpe(returns: np.ndarray, n_trials: int, sr_variance: float) -> float:
    """Bailey & Lopez de Prado (2014): P(true Sharpe > 0) after selecting the best of
    ``n_trials`` strategies whose (per-period) Sharpe ratios have variance ``sr_variance``."""
    sd = returns.std(ddof=1)
    if len(returns) < 3 or sd == 0 or n_trials < 2:
        return float("nan")
    sr = returns.mean() / sd
    expected_max = np.sqrt(sr_variance) * (
        (1 - EULER_GAMMA) * stats.norm.ppf(1 - 1 / n_trials)
        + EULER_GAMMA * stats.norm.ppf(1 - 1 / (n_trials * np.e))
    )
    skew = stats.skew(returns)
    kurt = stats.kurtosis(returns, fisher=False)
    denom = np.sqrt(max(1 - skew * sr + (kurt - 1) / 4 * sr**2, 1e-12))
    return float(stats.norm.cdf((sr - expected_max) * np.sqrt(len(returns) - 1) / denom))


def deflated_sharpe_table(
    daily: pd.DataFrame, span: pd.DatetimeIndex, n_parameter_sets: int
) -> pd.DataFrame:
    """DSR of every strategy book on ``span``: absolute, and of its excess over the matching
    buy & hold. ``n_trials`` = books tried; the strict column uses every parameter set."""
    d = daily.loc[daily.index.isin(span)]
    cols = _strategy_columns(d)
    excess = {c: d[c] - d[(c[0], "buy & hold", c[2])] for c in cols}
    sr_abs = [d[c].mean() / d[c].std(ddof=1) for c in cols]
    sr_exc = [e.mean() / e.std(ddof=1) for e in excess.values()]
    var_abs, var_exc = float(np.var(sr_abs, ddof=1)), float(np.var(sr_exc, ddof=1))
    n = len(cols)
    rows = []
    for c in cols:
        r, e = d[c].to_numpy(), excess[c].to_numpy()
        rows.append(
            {
                "timeframe": c[0],
                "book": c[1],
                "sizing": c[2],
                "sharpe": sweep.sharpe_np(r, DAILY_ANN),
                "excess_sharpe": sweep.sharpe_np(e, DAILY_ANN),
                "dsr": deflated_sharpe(r, n, var_abs),
                "dsr_excess": deflated_sharpe(e, n, var_exc),
                "dsr_excess_all_trials": deflated_sharpe(e, n_parameter_sets, var_exc),
            }
        )
    table = pd.DataFrame(rows)
    table["significant"] = table["dsr"] >= config.DSR_SIGNIFICANCE
    table["beats_hold_significantly"] = table["dsr_excess"] >= config.DSR_SIGNIFICANCE
    return table.sort_values("dsr_excess", ascending=False).reset_index(drop=True)


def _sens_job(timeframe: str, variant_name: str, gap: float, cooldown: int, adx: float) -> tuple:
    bars, inputs = history._bars_and_inputs(timeframe)
    variant = dataclasses.replace(
        variants.BY_NAME[variant_name], min_gap_atr=gap, cooldown=cooldown, adx_level=adx
    )
    runs = variants.run_variant(bars, variant, inputs)
    return timeframe, variant_name, gap, cooldown, adx, ensemble.ensemble_exposure(runs, bars.index)


def sensitivity_settings() -> list[tuple[str, str, float, int, float]]:
    out = []
    for tf in config.SENS_TIMEFRAMES:
        out.append((tf, "long_flat", 0.0, 0, config.ADX_TREND))
        for gap in config.SENS_GAP_ATR:
            for cd in config.SENS_COOLDOWN:
                out.append((tf, "long_flat_gap_cd", gap, cd, config.ADX_TREND))
        for adx in config.SENS_ADX:
            out.append((tf, "long_flat_adx", 0.0, 0, adx))
    return out


def sensitivity(workers: int = config.STUDY_WORKERS) -> pd.DataFrame:
    """Long/flat ensembles under every neighbouring setting; nothing is picked from this."""
    jobs = sensitivity_settings()
    with ProcessPoolExecutor(workers, mp_context=mp.get_context("spawn")) as pool:
        results = list(pool.map(_sens_job, *zip(*jobs, strict=True)))
    bars_by_tf = {tf: history._bars_and_inputs(tf)[0] for tf in config.SENS_TIMEFRAMES}
    rows = []
    for tf, name, gap, cd, adx, exposure in results:
        bars = bars_by_tf[tf]
        ann = config.annualization(config.TIMEFRAMES[tf])
        span = history.oos_span(bars)
        sizings = [("full", pd.Series(1.0, index=bars.index))] + [
            (f"vol {vt:.0%}", ensemble.vol_weight(bars["close"], config.TIMEFRAMES[tf], vt))
            for vt in config.SENS_VOL_TARGETS
        ]
        for label, weight in sizings:
            strat = history.book_returns(bars, exposure * weight, config.COST_BPS, config.EXECUTION)
            hold = history.book_returns(bars, weight, config.COST_BPS, config.EXECUTION)
            s, h = strat.loc[span], hold.loc[span]
            rows.append(
                {
                    "timeframe": tf,
                    "book": name,
                    "gap_atr": gap,
                    "cooldown": cd,
                    "adx": adx,
                    "sizing": label,
                    "sharpe": sweep.sharpe(s, ann),
                    "bench_sharpe": sweep.sharpe(h, ann),
                    "max_drawdown": sweep.max_drawdown(s),
                    "bench_max_drawdown": sweep.max_drawdown(h),
                }
            )
    table = pd.DataFrame(rows)
    table["sharpe_margin"] = table["sharpe"] - table["bench_sharpe"]
    table["passes"] = (table["sharpe_margin"] > 0) & (
        table["max_drawdown"] > table["bench_max_drawdown"]
    )
    return table


def sensitivity_summary(table: pd.DataFrame) -> pd.DataFrame:
    g = table.groupby(["book", "timeframe"], sort=False)
    return pd.DataFrame(
        {
            "settings": g.size(),
            "pass_rate": g["passes"].mean(),
            "worst_sharpe_margin": g["sharpe_margin"].min(),
            "median_sharpe_margin": g["sharpe_margin"].median(),
            "worst_max_drawdown": g["max_drawdown"].min(),
        }
    ).reset_index()
