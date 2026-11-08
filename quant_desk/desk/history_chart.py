"""Charts for the multi-year study: walk-forward Sharpe, Sharpe heatmaps, out-of-sample equity."""

from __future__ import annotations

from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.colors import LinearSegmentedColormap, TwoSlopeNorm
from matplotlib.ticker import FuncFormatter, NullFormatter

from desk import config
from desk.chart import AXIS, INK, LONG, MUTED, NEUTRAL, SHORT
from desk.history import Study

# Reference categorical palette, fixed order (never cycled).
CATEGORICAL = ("#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6")
TF_COLOURS = dict(zip(config.TIMEFRAMES, CATEGORICAL, strict=False))
SHARPE_FLOOR = 3.0  # bars below -3 are clipped and labelled with their value


def wf_sharpe_chart(summary: pd.DataFrame, path: Path) -> Path:
    """Out-of-sample walk-forward Sharpe per variant and timeframe, at 2 and 10 bps."""
    variant_names = list(dict.fromkeys(summary["variant"]))
    groups = [*variant_names, "buy & hold"]
    tfs = list(config.TIMEFRAMES)
    width = 0.8 / len(tfs)
    fig, axes = plt.subplots(1, 2, figsize=(16, 5.6), sharey=True)
    for ax, col, title in (
        (axes[0], "wf_oos_sharpe", f"fees {config.FEE_BPS:g} bps"),
        (axes[1], "wf_oos_sharpe_10bps", "fees 10 bps (standard taker)"),
    ):
        for i, tf in enumerate(tfs):
            sub = summary[summary["timeframe"] == tf].set_index("variant")
            vals = [*sub.loc[variant_names, col].tolist(), float(sub["hold_oos_sharpe"].iloc[0])]
            x = np.arange(len(groups)) + (i - (len(tfs) - 1) / 2) * width
            shown = np.clip(vals, -SHARPE_FLOOR, None)
            ax.bar(x, shown, width=width * 0.92, color=TF_COLOURS[tf], label=tf, zorder=3)
            for xi, v in zip(x, vals, strict=True):
                if v < -SHARPE_FLOOR:  # clipped: print the real value
                    ax.text(
                        xi,
                        -SHARPE_FLOOR + 0.08,
                        f"{v:.1f}",
                        ha="center",
                        va="bottom",
                        rotation=90,
                        fontsize=8,
                        color="#ffffff",
                        zorder=4,
                    )
        ax.set_ylim(-SHARPE_FLOOR - 0.5, 1.6)
        ax.axhline(0, color=AXIS, lw=1.0, zorder=2)
        ax.set_xticks(range(len(groups)), groups, rotation=30, ha="right")
        ax.set_title(f"Walk-forward out-of-sample Sharpe, {title}", loc="left")
        ax.grid(axis="x", visible=False)
    axes[0].set_ylabel(f"annualised Sharpe, stitched W2–W8 (axis clipped at −{SHARPE_FLOOR:g})")
    axes[1].legend(title="bar size", loc="lower right", fontsize=9, title_fontsize=9)
    fig.savefig(path, dpi=130, bbox_inches="tight")
    plt.close(fig)
    return path


def heatmaps_chart(study: Study, family: str, path: Path) -> Path:
    """Sharpe over each grid (rows: bar size, columns: variant); dots mark plateaus."""
    cells = [c for c in study.cells if c.variant.family == family]
    tfs = list(dict.fromkeys(c.timeframe for c in cells))
    names = list(dict.fromkeys(c.variant.name for c in cells))
    lim = float(
        np.nanpercentile(np.abs(np.concatenate([c.table["sharpe"].to_numpy() for c in cells])), 98)
    )
    cmap = LinearSegmentedColormap.from_list("div", [SHORT, NEUTRAL, LONG])
    norm = TwoSlopeNorm(0.0, -lim, lim)
    fig, axes = plt.subplots(
        len(tfs), len(names), figsize=(3.4 * len(names) + 1, 3.0 * len(tfs)), squeeze=False
    )
    im = None
    for cell in cells:
        ax = axes[tfs.index(cell.timeframe)][names.index(cell.variant.name)]
        ax.grid(False)
        a, b = cell.variant.axes
        pivot = cell.table.pivot(index=a, columns=b, values="sharpe").sort_index(ascending=False)
        im = ax.imshow(pivot.values, cmap=cmap, norm=norm, aspect="auto", interpolation="nearest")
        rows = {v: i for i, v in enumerate(pivot.index)}
        cols = {v: j for j, v in enumerate(pivot.columns)}
        plateaus = cell.table[cell.table["label"] == "plateau"]
        if not plateaus.empty:
            ax.scatter(
                [cols[v] for v in plateaus[b]],
                [rows[v] for v in plateaus[a]],
                s=7,
                color=INK,
                linewidths=0,
            )
        ax.set_title(
            f"{cell.variant.name} · {cell.timeframe} — {len(plateaus)} plateaus",
            loc="left",
            fontsize=9,
        )
        step = max(1, len(pivot.columns) // 6)
        ax.set_xticks(range(0, len(pivot.columns), step), [f"{v:g}" for v in pivot.columns[::step]])
        ystep = max(1, len(pivot.index) // 6)
        ax.set_yticks(range(0, len(pivot.index), ystep), [f"{v:g}" for v in pivot.index[::ystep]])
        ax.tick_params(labelsize=7)
        if names.index(cell.variant.name) == 0:
            ax.set_ylabel(a, fontsize=8)
        if tfs.index(cell.timeframe) == len(tfs) - 1:
            ax.set_xlabel(b, fontsize=8)
    cb = fig.colorbar(im, ax=axes, fraction=0.015, pad=0.01)
    cb.set_label(f"annualised Sharpe, fees {config.FEE_BPS:g} bps (clipped at ±{lim:.1f})")
    cb.outline.set_visible(False)
    label = (
        "EMA trend variants (fast × slow)" if family == "trend" else "Bollinger fade (window × k)"
    )
    fig.suptitle(
        f"{label}: Sharpe over the whole grid, Jan 2022 – Nov 2026.\n"
        "Dots = plateau (neighbours hold ≥ 50% and ≥ 6/8 windows positive)",
        x=0.01,
        ha="left",
        fontweight="bold",
        color=INK,
    )
    fig.savefig(path, dpi=130, bbox_inches="tight")
    plt.close(fig)
    return path


def wf_equity_chart(study: Study, path: Path) -> Path:
    """Stitched out-of-sample walk-forward equity per variant (log scale), one panel per bar."""
    tfs = list(study.bars)
    fig, axes = plt.subplots(len(tfs), 1, figsize=(16, 4.2 * len(tfs)), sharex=True)
    for ax, tf in zip(np.atleast_1d(axes), tfs, strict=True):
        cells = [c for c in study.cells if c.timeframe == tf]
        idx = cells[0].wf.oos_returns.index
        hold = (1 + study.buy_and_hold(tf, idx)).cumprod()
        ax.plot(hold.index, hold, color=MUTED, lw=1.2, ls="--", label="buy & hold")
        for cell, colour in zip(cells, CATEGORICAL, strict=False):
            eq = (1 + cell.wf.oos_returns).cumprod()
            ax.plot(eq.index, eq, color=colour, lw=1.4, label=cell.variant.name)
        ax.set_yscale("log")
        ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v:g}×"))
        ax.yaxis.set_minor_formatter(NullFormatter())
        ax.axhline(1.0, color=AXIS, lw=0.8)
        ax.set_ylabel("equity (×, log)")
        ax.set_title(
            f"{tf} bars — walk-forward out-of-sample equity (each window traded with the combo "
            f"that was best on the previous window), fees {config.FEE_BPS:g} bps",
            loc="left",
            fontsize=11,
        )
        ax.legend(loc="upper left", ncols=4, fontsize=8)
    fig.savefig(path, dpi=130, bbox_inches="tight")
    plt.close(fig)
    return path
