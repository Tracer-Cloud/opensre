"""Multi-year study: every variant x every timeframe -> sweep, plateaus, fee stress, walk-forward."""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass

import numpy as np
import pandas as pd

from desk import config, data, filters, sweep, variants
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
    best_plateau_returns: pd.Series | None  # full-period returns of the best plateau combo


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

    plateaus = table[table["label"] == "plateau"]
    best_plateau = None
    if not plateaus.empty:
        top = plateaus.iloc[0]
        best_plateau = runs[(top[variant.axes[0]], top[variant.axes[1]])].returns
    return CellResult(
        timeframe=timeframe,
        variant=variant,
        table=table,
        wf=sweep.walk_forward(runs, bars.index, config.HISTORY_WINDOWS, ann),
        wf_stress=sweep.walk_forward(stressed, bars.index, config.HISTORY_WINDOWS, ann),
        best_plateau_returns=best_plateau,
    )


@dataclass(frozen=True)
class Study:
    bars: dict[str, pd.DataFrame]
    cells: list[CellResult]

    def buy_and_hold(self, timeframe: str, index: pd.DatetimeIndex | None = None) -> pd.Series:
        close = self.bars[timeframe]["close"]
        r = close.pct_change().fillna(0.0)
        return r if index is None else r.reindex(index, fill_value=0.0)


def run_study(timeframes: tuple[str, ...] | None = None) -> Study:
    history = data.load(config.SYMBOL_HISTORY)
    bars_by_tf: dict[str, pd.DataFrame] = {}
    cells: list[CellResult] = []
    for tf in timeframes or tuple(config.TIMEFRAMES):
        bars = data.resample_closed(history, config.TIMEFRAMES[tf])
        bars_by_tf[tf] = bars
        inputs = filters.regime_inputs(bars)
        for variant in variants.VARIANTS:
            cell = run_cell(bars, inputs, tf, variant)
            counts = cell.table["label"].value_counts()
            logger.info(
                "%-4s %-16s plateau %3d  peak %3d  loser %3d  | WF OOS Sharpe %.2f",
                tf,
                variant.name,
                counts.get("plateau", 0),
                counts.get("peak", 0),
                counts.get("loser", 0),
                sweep.sharpe(cell.wf.oos_returns, config.annualization(config.TIMEFRAMES[tf])),
            )
            data.store(cell.table, f"{config.SYMBOL}_{tf}_{variant.name}_history_sweep")
            cells.append(cell)
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
