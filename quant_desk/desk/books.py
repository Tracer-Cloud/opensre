"""Shared plumbing for the hold-out studies: load stored 4h bars at any bar size, compute
whole-grid ensemble exposures in worker processes, and score a book on a span."""

from __future__ import annotations

import multiprocessing as mp
from concurrent.futures import ProcessPoolExecutor

import pandas as pd

from desk import config, data, ensemble, filters, history, sweep, variants

ALL_BOOKS = tuple(name for pair in config.LONG_SHORT_PAIRS for name in pair)


def load_bars(arctic_symbol: str, timeframe: str) -> pd.DataFrame:
    """Stored 4h bars (close-stamped), resampled to ``timeframe`` if needed."""
    bars = data.load(arctic_symbol)
    minutes = config.TIMEFRAMES[timeframe]
    return bars if minutes == 240 else data.resample_closed(bars, minutes)


def exposure_job(
    arctic_symbol: str, timeframe: str, names: tuple[str, ...]
) -> tuple[str, str, pd.DataFrame, dict[str, pd.Series]]:
    bars = load_bars(arctic_symbol, timeframe)
    if len(bars) < 2 * max(config.SLOW_SPANS):
        return arctic_symbol, timeframe, bars, {}
    inputs = filters.regime_inputs(bars)
    exposures = {
        name: ensemble.ensemble_exposure(
            variants.run_variant(bars, variants.BY_NAME[name], inputs), bars.index
        )
        for name in names
    }
    return arctic_symbol, timeframe, bars, exposures


def run_exposures(
    jobs: list[tuple[str, str]],
    names: tuple[str, ...] = ALL_BOOKS,
    workers: int = config.STUDY_WORKERS,
) -> dict[tuple[str, str], tuple[pd.DataFrame, dict[str, pd.Series]]]:
    """``{(arctic_symbol, timeframe): (bars, {variant: exposure})}`` for every job."""
    if not jobs:
        return {}
    syms, tfs = zip(*jobs, strict=True)
    with ProcessPoolExecutor(workers, mp_context=mp.get_context("spawn")) as pool:
        results = list(pool.map(exposure_job, syms, tfs, [names] * len(jobs)))
    return {(s, tf): (bars, exp) for s, tf, bars, exp in results}


def sized(bars: pd.DataFrame, exposure: pd.Series, sizing: str, timeframe: str) -> pd.Series:
    if sizing == "raw":
        return exposure
    return exposure * ensemble.vol_weight(bars["close"], config.TIMEFRAMES[timeframe])


def returns(bars: pd.DataFrame, exposure: pd.Series) -> pd.Series:
    return history.book_returns(bars, exposure, config.COST_BPS, config.EXECUTION)


def score(r: pd.Series, ann: float) -> dict[str, float]:
    years = len(r) / ann**2
    total = float((1 + r).prod())
    return {
        "sharpe": sweep.sharpe(r, ann),
        "cagr": total ** (1 / years) - 1 if total > 0 and years > 0 else -1.0,
        "max_drawdown": sweep.max_drawdown(r),
    }


def annualization(timeframe: str) -> float:
    return config.annualization(config.TIMEFRAMES[timeframe])


def span_mask(
    index: pd.DatetimeIndex,
    start: str | pd.Timestamp | None,
    end: str | pd.Timestamp | None,
    warmup_days: int = 120,
) -> pd.Series:
    """Bars inside [start, end), never earlier than ``warmup_days`` after the series begins."""
    lo = index[0] + pd.Timedelta(days=warmup_days)
    if start is not None:
        lo = max(lo, pd.Timestamp(start, tz="UTC"))
    hi = pd.Timestamp(end, tz="UTC") if end is not None else index[-1] + pd.Timedelta(seconds=1)
    return pd.Series((index >= lo) & (index < hi), index=index)
