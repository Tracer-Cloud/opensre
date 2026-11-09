"""Charts for the multi-year study: walk-forward Sharpe, Sharpe heatmaps, out-of-sample equity."""

from __future__ import annotations

from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.colors import LinearSegmentedColormap, TwoSlopeNorm
from matplotlib.ticker import FuncFormatter, NullFormatter

from desk import config, history
from desk.chart import AXIS, INK, LONG, MUTED, NEUTRAL, SHORT

# Reference categorical palette, fixed order (never cycled).
CATEGORICAL = (
    "#2a78d6",
    "#eb6834",
    "#1baf7a",
    "#eda100",
    "#e87ba4",
    "#008300",
    "#6250d6",
    "#e34948",
)
TF_COLOURS = dict(zip(config.TIMEFRAMES, CATEGORICAL, strict=False))
SHARPE_FLOOR = 3.0  # bars below -3 are clipped and labelled with their value


def wf_sharpe_chart(summary: pd.DataFrame, path: Path) -> Path:
    """Out-of-sample walk-forward Sharpe per variant and timeframe, headline vs optimistic cost."""
    variant_names = list(dict.fromkeys(summary["variant"]))
    groups = [*variant_names, "buy & hold"]
    tfs = list(config.TIMEFRAMES)
    width = 0.8 / len(tfs)
    fig, axes = plt.subplots(1, 2, figsize=(16, 5.6), sharey=True)
    for ax, col, title in (
        (axes[0], "wf_oos_sharpe", config.COST_LABEL),
        (axes[1], "wf_oos_sharpe_optimistic", config.OPTIMISTIC_LABEL),
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
                        fontsize=6,
                        color="#ffffff",
                        zorder=4,
                    )
        ax.set_ylim(-SHARPE_FLOOR - 0.5, 1.6)
        ax.axhline(0, color=AXIS, lw=1.0, zorder=2)
        ax.set_xticks(range(len(groups)), groups, rotation=30, ha="right")
        ax.set_title(f"Walk-forward OOS Sharpe — {title}", loc="left", fontsize=10)
        ax.grid(axis="x", visible=False)
    axes[0].set_ylabel(f"annualised Sharpe, stitched W2–W8 (axis clipped at −{SHARPE_FLOOR:g})")
    axes[1].legend(title="bar size", loc="lower right", fontsize=9, title_fontsize=9)
    fig.savefig(path, dpi=130, bbox_inches="tight")
    plt.close(fig)
    return path


def heatmaps_chart(study: history.Study, family: str, path: Path) -> Path:
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
    cb.set_label(f"annualised Sharpe at {config.COST_BPS:g} bps (clipped at ±{lim:.1f})")
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


def wf_equity_chart(study: history.Study, path: Path) -> Path:
    """Stitched out-of-sample walk-forward equity per variant (log scale), one panel per bar."""
    tfs = list(study.bars)
    fig, axes = plt.subplots(len(tfs), 1, figsize=(16, 4.2 * len(tfs)), sharex=True)
    for ax, tf in zip(np.atleast_1d(axes), tfs, strict=True):
        cells = [c for c in study.cells if c.timeframe == tf and c.variant.family == "trend"]
        if len(cells) > len(CATEGORICAL):
            raise ValueError("more trend variants than palette slots; facet the chart")
        idx = cells[0].wf.oos_returns.index
        hold = (1 + study.buy_and_hold(tf, idx)).cumprod()
        ax.plot(hold.index, hold, color=MUTED, lw=1.2, ls="--", label="buy & hold")
        for cell, colour in zip(cells, CATEGORICAL[: len(cells)], strict=True):
            eq = (1 + cell.wf.oos_returns).cumprod()
            ax.plot(eq.index, eq, color=colour, lw=1.4, label=cell.variant.name)
        ax.set_yscale("log")
        ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v:g}×"))
        ax.yaxis.set_minor_formatter(NullFormatter())
        ax.axhline(1.0, color=AXIS, lw=0.8)
        ax.set_ylabel("equity (×, log)")
        ax.set_title(
            f"{tf} bars — walk-forward out-of-sample equity (each window traded with the combo "
            f"that was best on the previous window), {config.COST_BPS:g} bps",
            loc="left",
            fontsize=11,
        )
        ax.legend(loc="upper left", ncols=4, fontsize=8)
    fig.savefig(path, dpi=130, bbox_inches="tight")
    plt.close(fig)
    return path


CANDIDATE_BOOKS = (
    ("buy & hold", "vol"),
    ("ensemble always_in", "vol"),
    ("ensemble long_flat", "raw"),
    ("ensemble long_flat", "vol"),
    ("ensemble long_flat_gap_cd", "vol"),
)


def candidates_chart(study: history.Study, path: Path) -> Path:
    """Out-of-sample equity of fixed, pre-chosen grid ensembles vs both buy & holds (log)."""
    tfs = list(study.bars)
    fig, axes = plt.subplots(len(tfs), 1, figsize=(16, 4.2 * len(tfs)), sharex=True)
    for ax, tf in zip(np.atleast_1d(axes), tfs, strict=True):
        bars = study.bars[tf]
        span = history.oos_span(bars)
        by_key = {(n, s): e for n, s, e in history.books(study, tf)}
        hold_r = history.book_returns(
            bars, by_key[("buy & hold", "raw")], config.COST_BPS, config.EXECUTION
        )
        hold = (1 + hold_r.loc[span]).cumprod()
        ax.plot(hold.index, hold, color=MUTED, lw=1.2, ls="--", label="buy & hold")
        for (name, sizing), colour in zip(CANDIDATE_BOOKS, CATEGORICAL, strict=False):
            r = history.book_returns(
                bars, by_key[(name, sizing)], config.COST_BPS, config.EXECUTION
            )
            eq = (1 + r.loc[span]).cumprod()
            label = f"{name} ({'vol-targeted' if sizing == 'vol' else 'full size'})"
            ax.plot(eq.index, eq, color=colour, lw=1.5, label=label)
        ax.set_yscale("log")
        ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v:g}×"))
        ax.yaxis.set_minor_formatter(NullFormatter())
        ax.axhline(1.0, color=AXIS, lw=0.8)
        ax.set_ylabel("equity (×, log)")
        ax.set_title(
            f"{tf} bars — whole-grid ensembles (no parameter picked), W2–W8, {config.COST_BPS:g} bps, next-open fills",
            loc="left",
            fontsize=11,
        )
    handles, labels = np.atleast_1d(axes)[0].get_legend_handles_labels()
    fig.legend(handles, labels, loc="upper left", ncols=3, fontsize=9, bbox_to_anchor=(0.06, 1.0))
    fig.subplots_adjust(top=0.95)
    fig.savefig(path, dpi=130, bbox_inches="tight")
    plt.close(fig)
    return path


PORTFOLIO_LINES = (
    ("buy & hold", "vol"),
    ("ensemble long_flat", "vol"),
    ("ensemble long_flat_gap_cd", "vol"),
    ("ensemble long_flat_adx", "vol"),
)


def portfolio_chart(curves: dict[tuple[str, str, str], pd.Series], path: Path) -> Path:
    """Equal-weight multi-coin portfolios (W2–W8, daily) vs the buy & hold portfolio, log."""
    tfs = list(dict.fromkeys(tf for tf, _, _ in curves))
    fig, axes = plt.subplots(len(tfs), 1, figsize=(16, 4.2 * len(tfs)), sharex=True)
    for ax, tf in zip(np.atleast_1d(axes), tfs, strict=True):
        hold = (1 + curves[(tf, "buy & hold", "raw")]).cumprod()
        ax.plot(hold.index, hold, color=MUTED, lw=1.2, ls="--", label="buy & hold (full size)")
        for (book, sizing), colour in zip(
            PORTFOLIO_LINES, CATEGORICAL[: len(PORTFOLIO_LINES)], strict=True
        ):
            eq = (1 + curves[(tf, book, sizing)]).cumprod()
            ax.plot(eq.index, eq, color=colour, lw=1.5, label=f"{book} (vol-targeted)")
        ax.set_yscale("log")
        ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v:g}×"))
        ax.yaxis.set_minor_formatter(NullFormatter())
        ax.axhline(1.0, color=AXIS, lw=0.8)
        ax.set_ylabel("equity (×, log)")
        ax.set_title(
            f"{tf} bars — equal-weight portfolio of {len(config.ASSETS)} coins, W2–W8, "
            f"{config.COST_LABEL}",
            loc="left",
            fontsize=11,
        )
    handles, labels = np.atleast_1d(axes)[0].get_legend_handles_labels()
    fig.legend(handles, labels, loc="upper left", ncols=3, fontsize=9, bbox_to_anchor=(0.06, 1.0))
    fig.subplots_adjust(top=0.93)
    fig.savefig(path, dpi=130, bbox_inches="tight")
    plt.close(fig)
    return path


def holdouts_chart(univ_curves: dict, expos: dict, path: Path) -> Path:
    """Left: survivorship-free universe (8h). Right: Bitstamp BTC 2013–2017 (4h), never seen."""
    from desk import books, cross_venue  # local: only this chart needs the hold-out plumbing

    fig, axes = plt.subplots(1, 2, figsize=(17, 5.6))
    lines = (
        ("buy & hold", "raw", MUTED, "--", "buy & hold"),
        ("buy & hold", "vol", CATEGORICAL[0], "-", "buy & hold (vol-targeted)"),
        ("long_flat_gap_cd", "vol", CATEGORICAL[1], "-", "long_flat_gap_cd (vol-targeted)"),
        ("gap_cooldown", "vol", CATEGORICAL[2], "-", "gap_cooldown long/short (vol-targeted)"),
    )
    ax = axes[0]
    for book, sizing, colour, ls, label in lines:
        r = univ_curves[("point-in-time", "8h", book, sizing)]
        ax.plot(r.index, (1 + r).cumprod(), color=colour, ls=ls, lw=1.5, label=label)
    surv = univ_curves[("survivors only", "8h", "long_flat_gap_cd", "vol")]
    ax.plot(
        surv.index,
        (1 + surv).cumprod(),
        color=CATEGORICAL[1],
        ls=":",
        lw=1.3,
        label="long_flat_gap_cd, survivors-only universe",
    )
    ax.set_title(
        "Point-in-time top-10 universe incl. delisted coins, 8h, W2–W8", loc="left", fontsize=11
    )

    ax = axes[1]
    bars, exp = expos[(cross_venue.venue_symbol("bitstamp"), "4h")]
    mask = books.span_mask(bars.index, None, cross_venue.PRE_BINANCE_END)
    for book, sizing, colour, ls, label in lines:
        base = pd.Series(1.0, index=bars.index) if book == "buy & hold" else exp[book]
        r = books.returns(bars, books.sized(bars, base, sizing, "4h"))[mask]
        ax.plot(r.index, (1 + r).cumprod(), color=colour, ls=ls, lw=1.5, label=label)
    ax.set_title(
        "BTC/USD on Bitstamp 2013–2017, 4h — never seen by any decision", loc="left", fontsize=11
    )
    for ax in axes:
        ax.set_yscale("log")
        ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v:g}×"))
        ax.yaxis.set_minor_formatter(NullFormatter())
        ax.axhline(1.0, color=AXIS, lw=0.8)
        ax.set_ylabel("equity (×, log)")
    handles, labels = axes[0].get_legend_handles_labels()
    fig.legend(handles, labels, loc="upper left", ncols=3, fontsize=9, bbox_to_anchor=(0.04, 1.06))
    fig.text(0.04, -0.02, config.COST_LABEL, fontsize=9, color=MUTED)
    fig.savefig(path, dpi=130, bbox_inches="tight")
    plt.close(fig)
    return path
