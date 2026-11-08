"""Multi-year study: every variant x every timeframe -> sweep, plateaus, fee stress, walk-forward."""

from __future__ import annotations

import logging
import math
import multiprocessing as mp
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass

import numpy as np
import pandas as pd

from desk import config, data, ensemble, filters, sweep, variants
from desk.strategy import ComboRun

logger = logging.getLogger(__name__)

STRESS_FEE_BPS = 10.0  # Binance spot taker fee without discounts


@dataclass(frozen=True)
class CellResult:
    """One (timeframe, variant) sweep."""

    timeframe: str
    variant: variants.Variant
    table: pd.DataFrame  # labelled sweep at FEE_BPS, plus sharpe/label at STRESS_FEE_BPS
    wf: sweep.WalkForward
    wf_stress: sweep.WalkForward
    exposure: pd.Series  # ensemble: average position over the whole grid, in [-1, 1]


def min_positive_windows() -> int:
    return math.ceil(config.HISTORY_MIN_POSITIVE_SHARE * config.HISTORY_WINDOWS)


def at_fee(runs: dict[tuple[float, float], ComboRun], fee_bps: float) -> dict:
    """Re-price the same fills at another fee (fees scale linearly with the bps rate)."""
    scale = fee_bps / config.FEE_BPS
    out = {}
    for key, run in runs.items():
        fee = run.fills["fee"].reindex(run.returns.index, fill_value=0.0)
        out[key] = ComboRun(
            params=run.params,
            returns=run.returns - fee * (scale - 1.0),
            positions=run.positions,
            fills=run.fills.assign(fee=run.fills["fee"] * scale),
        )
    return out


def _label(runs: dict, index: pd.DatetimeIndex, ann: float, axes: tuple[str, str]) -> pd.DataFrame:
    table = sweep.summarise(runs, index, config.HISTORY_WINDOWS, ann, axes)
    return sweep.label_plateaus(table, axes, min_positive_windows(), config.HISTORY_WINDOWS)


def run_cell(
    bars: pd.DataFrame, inputs: pd.DataFrame, timeframe: str, variant: variants.Variant
) -> CellResult:
    ann = config.annualization(config.TIMEFRAMES[timeframe])
    runs = variants.run_variant(bars, variant, inputs)
    stressed = at_fee(runs, STRESS_FEE_BPS)
    table = _label(runs, bars.index, ann, variant.axes)
    stress = _label(stressed, bars.index, ann, variant.axes)
    keyed = stress.set_index(list(variant.axes))[["sharpe", "label"]]
    keyed.columns = ["sharpe_10bps", "label_10bps"]
    table = table.join(keyed, on=list(variant.axes))

    return CellResult(
        timeframe=timeframe,
        variant=variant,
        table=table,
        wf=sweep.walk_forward(runs, bars.index, config.HISTORY_WINDOWS, ann),
        wf_stress=sweep.walk_forward(stressed, bars.index, config.HISTORY_WINDOWS, ann),
        exposure=ensemble.ensemble_exposure(runs, bars.index),
    )


@dataclass(frozen=True)
class Study:
    bars: dict[str, pd.DataFrame]
    cells: list[CellResult]

    def buy_and_hold(self, timeframe: str, index: pd.DatetimeIndex | None = None) -> pd.Series:
        close = self.bars[timeframe]["close"]
        r = close.pct_change().fillna(0.0)
        return r if index is None else r.reindex(index, fill_value=0.0)


def _bars_and_inputs(timeframe: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    bars = data.resample_closed(data.load(config.SYMBOL_HISTORY), config.TIMEFRAMES[timeframe])
    return bars, filters.regime_inputs(bars)


def _cell_job(timeframe: str, variant_name: str) -> CellResult:
    """Worker entry point: everything is rebuilt from ArcticDB inside the process."""
    bars, inputs = _bars_and_inputs(timeframe)
    return run_cell(bars, inputs, timeframe, variants.BY_NAME[variant_name])


def run_study(
    timeframes: tuple[str, ...] | None = None, workers: int = config.STUDY_WORKERS
) -> Study:
    """Every variant x every timeframe, ``workers`` cells at a time (slowest cells first)."""
    tfs = timeframes or tuple(config.TIMEFRAMES)
    bars_by_tf = {tf: _bars_and_inputs(tf)[0] for tf in tfs}
    jobs = [
        (tf, v.name)
        for tf in sorted(tfs, key=lambda t: config.TIMEFRAMES[t])
        for v in variants.VARIANTS
    ]
    with ProcessPoolExecutor(workers, mp_context=mp.get_context("spawn")) as pool:
        results = list(pool.map(_cell_job, *zip(*jobs, strict=True)))
    order = {
        (tf, v.name): i for i, (tf, v) in enumerate((t, v) for t in tfs for v in variants.VARIANTS)
    }
    cells = sorted(results, key=lambda c: order[(c.timeframe, c.variant.name)])
    for cell in cells:
        counts = cell.table["label"].value_counts()
        logger.info(
            "%-4s %-16s plateau %3d  peak %3d  loser %3d  | WF OOS Sharpe %.2f",
            cell.timeframe,
            cell.variant.name,
            counts.get("plateau", 0),
            counts.get("peak", 0),
            counts.get("loser", 0),
            sweep.sharpe(
                cell.wf.oos_returns, config.annualization(config.TIMEFRAMES[cell.timeframe])
            ),
        )
        data.store(
            cell.table, f"{config.SYMBOL}_{cell.timeframe}_{cell.variant.name}_history_sweep"
        )
    return Study(bars=bars_by_tf, cells=cells)


def summary(study: Study) -> pd.DataFrame:
    """One row per (timeframe, variant) with the numbers that decide whether to keep going."""
    rows = []
    for cell in study.cells:
        tf, t = cell.timeframe, cell.table
        ann = config.annualization(config.TIMEFRAMES[tf])
        oos = cell.wf.oos_returns
        hold_oos = study.buy_and_hold(tf, oos.index)
        plateaus = t[t["label"] == "plateau"]
        best_p = plateaus.iloc[0] if not plateaus.empty else None
        axes = cell.variant.axes
        rows.append(
            {
                "timeframe": tf,
                "variant": cell.variant.name,
                "combos": len(t),
                "positive": int((t["sharpe"] > 0).sum()),
                "plateaus": len(plateaus),
                "plateaus_10bps": int((t["label_10bps"] == "plateau").sum()),
                "best_plateau": (
                    f"{best_p[axes[0]]:g}/{best_p[axes[1]]:g}" if best_p is not None else "-"
                ),
                "best_plateau_sharpe": float(best_p["sharpe"]) if best_p is not None else np.nan,
                "best_plateau_sharpe_10bps": (
                    float(best_p["sharpe_10bps"]) if best_p is not None else np.nan
                ),
                "median_trades_per_year": float(
                    t["trades"].median() / (len(study.bars[tf]) / (ann**2))
                ),
                "median_exposure": float(t["exposure"].median()),
                "wf_oos_sharpe": sweep.sharpe(oos, ann),
                "wf_oos_return": float((1 + oos).prod() - 1),
                "wf_oos_sharpe_10bps": sweep.sharpe(cell.wf_stress.oos_returns, ann),
                "wf_steps_positive": int((cell.wf.steps["oos_return"] > 0).sum()),
                "wf_steps": len(cell.wf.steps),
                "wf_oos_max_drawdown": sweep.max_drawdown(oos),
                "hold_oos_sharpe": sweep.sharpe(hold_oos, ann),
                "hold_oos_max_drawdown": sweep.max_drawdown(hold_oos),
                "hold_oos_return": float((1 + hold_oos).prod() - 1),
            }
        )
    return pd.DataFrame(rows)


def _metrics(r: pd.Series, exposure: pd.Series, ann: float, n_windows: int) -> dict[str, float]:
    years = len(r) / ann**2
    total = float((1 + r).prod())
    slices = sweep.window_slices(len(r), n_windows)
    arr = r.to_numpy()
    return {
        "sharpe": sweep.sharpe(r, ann),
        "cagr": total ** (1 / years) - 1 if total > 0 else -1.0,
        "max_drawdown": sweep.max_drawdown(r),
        "positive_windows": int(sum(np.prod(1 + arr[s]) > 1 for s in slices)),
        "avg_exposure": float(exposure.abs().mean()),
        "turnover_per_year": float(exposure.diff().abs().sum() / years),
    }


def oos_span(bars: pd.DataFrame) -> pd.DatetimeIndex:
    """W2..W8: the span every walk-forward and candidate number is measured on."""
    return bars.index[sweep.window_slices(len(bars), config.HISTORY_WINDOWS)[1].start :]


def books(study: Study, timeframe: str) -> list[tuple[str, str, pd.Series]]:
    """(name, sizing, exposure) for both benchmarks and every trend variant's grid ensemble."""
    bars = study.bars[timeframe]
    weight = ensemble.vol_weight(bars["close"], config.TIMEFRAMES[timeframe])
    out: list[tuple[str, str, pd.Series]] = [
        ("buy & hold", "raw", pd.Series(1.0, index=bars.index)),
        ("buy & hold", "vol", weight),
    ]
    for cell in study.cells:
        if cell.timeframe == timeframe and cell.variant.family == "trend":
            out.append((f"ensemble {cell.variant.name}", "raw", cell.exposure))
            out.append((f"ensemble {cell.variant.name}", "vol", cell.exposure * weight))
    return out


def candidates(study: Study, fee_bps: float) -> pd.DataFrame:
    """Grid ensembles (raw and vol-targeted) of every trend variant vs matching buy & hold.

    Measured on the out-of-sample span W2..W8 (same span as the walk-forward), with
    ``HISTORY_WINDOWS - 1`` sub-windows. No parameter is picked anywhere.
    """
    rows = []
    for tf, bars in study.bars.items():
        ann = config.annualization(config.TIMEFRAMES[tf])
        span = oos_span(bars)
        for name, sizing, exposure in books(study, tf):
            r = ensemble.returns_from_exposure(bars["close"], exposure, fee_bps).loc[span]
            rows.append(
                {
                    "timeframe": tf,
                    "book": name,
                    "sizing": sizing,
                    "fee_bps": fee_bps,
                    **_metrics(r, exposure.loc[span], ann, config.HISTORY_WINDOWS - 1),
                }
            )
    table = pd.DataFrame(rows)
    bench = table[table["book"] == "buy & hold"].set_index(["timeframe", "sizing"])
    b_sharpe = bench["sharpe"].reindex(pd.MultiIndex.from_frame(table[["timeframe", "sizing"]]))
    b_dd = bench["max_drawdown"].reindex(pd.MultiIndex.from_frame(table[["timeframe", "sizing"]]))
    table["bench_sharpe"] = b_sharpe.to_numpy()
    table["bench_max_drawdown"] = b_dd.to_numpy()
    table["passes"] = (
        (table["book"] != "buy & hold")
        & (table["sharpe"] > table["bench_sharpe"])
        & (table["max_drawdown"] > table["bench_max_drawdown"])
    )
    return table
