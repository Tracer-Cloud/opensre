"""Static charts: trades on price, Sharpe heatmap, per-window equity, walk-forward."""

from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.dates as mdates  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from matplotlib.colors import LinearSegmentedColormap, TwoSlopeNorm  # noqa: E402

from desk import config, sweep  # noqa: E402
from desk.strategy import ComboRun  # noqa: E402

# Reference palette (light mode). Long/short is a polarity -> the diverging blue/red pair.
SURFACE = "#fcfcfb"
INK = "#0b0b0b"
INK_2 = "#52514e"
MUTED = "#898781"
GRID = "#e1e0d9"
AXIS = "#c3c2b7"
NEUTRAL = "#f0efec"
LONG = "#2a78d6"
SHORT = "#e34948"
SERIES = ("#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4")
EMA_FAST = "#eb6834"
EMA_SLOW = "#1baf7a"

plt.rcParams.update(
    {
        "figure.facecolor": SURFACE,
        "axes.facecolor": SURFACE,
        "axes.edgecolor": AXIS,
        "axes.labelcolor": INK_2,
        "axes.titlecolor": INK,
        "axes.titleweight": "bold",
        "axes.titlesize": 12,
        "axes.grid": True,
        "grid.color": GRID,
        "grid.linewidth": 0.6,
        "axes.spines.top": False,
        "axes.spines.right": False,
        "xtick.color": MUTED,
        "ytick.color": MUTED,
        "font.family": "DejaVu Sans",
        "legend.frameon": False,
        "legend.labelcolor": INK_2,
    }
)


def _equity(returns: pd.Series) -> pd.Series:
    return (1.0 + returns).cumprod()


def _date_axis(ax: plt.Axes, fmt: str = "%d %b") -> None:
    ax.xaxis.set_major_formatter(mdates.DateFormatter(fmt))


def _plot_fills(ax: plt.Axes, fills: pd.DataFrame, size: float = 46, label: bool = True) -> None:
    for side, colour, marker in (("LONG", LONG, "^"), ("SHORT", SHORT, "v")):
        f = fills[fills["side"] == side]
        ax.scatter(
            f.index,
            f["price"],
            marker=marker,
            s=size,
            color=colour,
            edgecolors=SURFACE,
            linewidths=1.2,
            zorder=5,
            label=f"{side} ({len(f)})" if label else None,
        )


def trades_chart(
    bars: pd.DataFrame,
    ind: pd.DataFrame,
    profile: pd.DataFrame,
    run: ComboRun,
    path: Path,
) -> Path:
    """Price + EMAs + Bollinger + every fill on its bar; RSI; equity; volume profile."""
    fast, slow = run.fast, run.slow
    fig = plt.figure(figsize=(16, 11))
    gs = fig.add_gridspec(3, 2, width_ratios=(6, 1), height_ratios=(5, 1.4, 2), wspace=0.02)
    ax = fig.add_subplot(gs[0, 0])
    axp = fig.add_subplot(gs[0, 1], sharey=ax)
    ax_rsi = fig.add_subplot(gs[1, 0], sharex=ax)
    ax_eq = fig.add_subplot(gs[2, 0], sharex=ax)

    ax.fill_between(
        ind.index, ind["bb_lower"], ind["bb_upper"], color=NEUTRAL, lw=0, label="Bollinger 20, 2σ"
    )
    ax.plot(ind.index, ind["close"], color=INK_2, lw=1.0, label="BTC/USDT close (15m)")
    ax.plot(ind.index, ind[f"ema_{fast}"], color=EMA_FAST, lw=1.4, label=f"EMA {fast}")
    ax.plot(ind.index, ind[f"ema_{slow}"], color=EMA_SLOW, lw=1.4, label=f"EMA {slow}")
    _plot_fills(ax, run.fills)
    ax.set_title(
        f"EMA {fast}/{slow} long/short on BTC/USDT 15m — every fill on the bar that fired it "
        f"({len(run.fills)} trades, {config.FEE_BPS:g} bps/side)",
        loc="left",
    )
    ax.set_ylabel("price (USDT)")
    ax.legend(loc="upper right", ncols=3, fontsize=9)
    plt.setp(ax.get_xticklabels(), visible=False)

    va = profile["value_area"]
    height = profile["price_high"] - profile["price_low"]
    axp.barh(
        profile["price_mid"],
        profile["volume"],
        height=height * 0.9,
        color=np.where(va, "#86b6ef", "#cde2fb"),
    )
    poc = profile[profile["poc"]].iloc[0]
    axp.barh(poc["price_mid"], poc["volume"], height=height.iloc[0] * 0.9, color="#1c5cab")
    axp.text(poc["volume"], poc["price_mid"], "  POC", va="center", color=INK_2, fontsize=8)
    axp.set_title("volume profile", loc="left", fontsize=10)
    axp.tick_params(labelleft=False)
    axp.set_xticks([])

    ax_rsi.plot(ind.index, ind["rsi"], color=INK_2, lw=0.9)
    for level in (30, 70):
        ax_rsi.axhline(level, color=AXIS, lw=0.8, ls="--")
    ax_rsi.set_ylim(0, 100)
    ax_rsi.set_ylabel("RSI 14")
    plt.setp(ax_rsi.get_xticklabels(), visible=False)

    strat = _equity(run.returns.reindex(bars.index, fill_value=0.0))
    hold = bars["close"] / bars["close"].iloc[0]
    ax_eq.plot(
        strat.index, strat, color=LONG, lw=1.6, label=f"strategy EMA {fast}/{slow} (net of fees)"
    )
    ax_eq.plot(hold.index, hold, color=MUTED, lw=1.2, ls="--", label="buy & hold")
    ax_eq.axhline(1.0, color=AXIS, lw=0.8)
    for w in sweep.window_bounds(bars.index)[1:]:
        for a in (ax, ax_rsi, ax_eq):
            a.axvline(w[0], color=AXIS, lw=0.8, ls=":")
    ax_eq.set_ylabel("equity (×)")
    ax_eq.legend(loc="upper left", fontsize=9)
    _date_axis(ax_eq)
    fig.savefig(path, dpi=130, bbox_inches="tight")
    plt.close(fig)
    return path


def zoom_chart(
    bars: pd.DataFrame, ind: pd.DataFrame, run: ComboRun, path: Path, days: int = 2
) -> Path:
    """Last ``days`` as candles so each fill can be checked against its exact bar."""
    start = bars.index[-1] - pd.Timedelta(days=days)
    b = bars.loc[bars.index > start]
    i = ind.loc[b.index]
    fills = run.fills.loc[run.fills.index > start]
    width = pd.Timedelta(minutes=10)

    fig, ax = plt.subplots(figsize=(16, 7))
    # Neutral candles (hollow up, grey down) so blue/red stays reserved for LONG/SHORT.
    up = b["close"] >= b["open"]
    for mask, colour in ((up, SURFACE), (~up, AXIS)):
        sub = b[mask]
        ax.vlines(sub.index, sub["low"], sub["high"], color=INK_2, lw=0.6, zorder=2)
        ax.bar(
            sub.index,
            (sub["close"] - sub["open"]).abs().clip(lower=b["close"].mean() * 2e-5),
            bottom=sub[["open", "close"]].min(axis=1),
            width=width,
            color=colour,
            edgecolor=INK_2,
            linewidth=0.4,
            zorder=3,
            align="center",
        )
    ax.plot(i.index, i[f"ema_{run.fast}"], color=EMA_FAST, lw=1.4, label=f"EMA {run.fast}")
    ax.plot(i.index, i[f"ema_{run.slow}"], color=EMA_SLOW, lw=1.4, label=f"EMA {run.slow}")
    # Markers sit just past the wick of the exact bar (x = bar close time); the fill price
    # itself is that bar's close and is printed in agent.log.
    pad = (b["high"].max() - b["low"].min()) * 0.025
    for side, colour, marker in (("LONG", LONG, "^"), ("SHORT", SHORT, "v")):
        f = fills[fills["side"] == side]
        is_short = side == "SHORT"
        y = b.loc[f.index, "high"] + pad if is_short else b.loc[f.index, "low"] - pad
        ax.scatter(
            f.index,
            y,
            marker=marker,
            s=90,
            color=colour,
            edgecolors=SURFACE,
            linewidths=1.2,
            zorder=5,
            label=f"{side} ({len(f)})",
        )
        for t, yy in y.items():
            ax.annotate(
                f"{'S' if is_short else 'L'} {t:%H:%M}",
                (t, yy),
                xytext=(0, 8 if is_short else -8),
                textcoords="offset points",
                ha="center",
                va="bottom" if is_short else "top",
                fontsize=8,
                color=INK_2,
            )
    ax.margins(y=0.08)
    ax.set_title(
        f"Last {days} days, 15m candles — each marker sits on the bar whose close fired the trade "
        "(same timestamps as agent.log)",
        loc="left",
    )
    ax.set_ylabel("price (USDT)")
    ax.legend(loc="upper left", ncols=4, fontsize=9)
    _date_axis(ax, "%d %b %H:%M")
    fig.savefig(path, dpi=130, bbox_inches="tight")
    plt.close(fig)
    return path


def heatmap_chart(table: pd.DataFrame, path: Path) -> Path:
    """Sharpe over the fast x slow grid; diverging around 0; peaks/plateaus annotated."""
    pivot = table.pivot(index="fast", columns="slow", values="sharpe").sort_index(ascending=False)
    cmap = LinearSegmentedColormap.from_list("div", [SHORT, NEUTRAL, LONG])
    lim = float(np.nanmax(np.abs(pivot.values)))
    fig, ax = plt.subplots(figsize=(15, 6))
    ax.grid(False)
    im = ax.imshow(
        pivot.values,
        cmap=cmap,
        norm=TwoSlopeNorm(0.0, -lim, lim),
        aspect="auto",
        interpolation="nearest",
    )
    ax.set_xticks(range(len(pivot.columns)), pivot.columns)
    ax.set_yticks(range(len(pivot.index)), pivot.index)
    ax.set_xlabel("slow EMA span (bars)")
    ax.set_ylabel("fast EMA span (bars)")
    pos = {(f, s): (r, c) for r, f in enumerate(pivot.index) for c, s in enumerate(pivot.columns)}
    for row in table[table["label"].isin(("plateau", "peak"))].itertuples():
        r, c = pos[(row.fast, row.slow)]
        tag = "P" if row.label == "plateau" else "▲"
        ax.text(c, r, f"{tag}\n{row.sharpe:.2f}", ha="center", va="center", fontsize=7, color=INK)
    best = table.iloc[0]
    r, c = pos[(best.fast, best.slow)]
    ax.add_patch(plt.Rectangle((c - 0.5, r - 0.5), 1, 1, fill=False, ec=INK, lw=1.6))
    cb = fig.colorbar(im, ax=ax, fraction=0.025, pad=0.01)
    cb.set_label("annualised Sharpe (net of fees)")
    cb.outline.set_visible(False)
    ax.set_title(
        f"Sharpe across {len(table)} combos — ▲ = isolated peak, P = plateau, box = #1 "
        "(blank cells: slow < 1.5 × fast)",
        loc="left",
    )
    fig.savefig(path, dpi=130, bbox_inches="tight")
    plt.close(fig)
    return path


def windows_chart(
    bars: pd.DataFrame,
    table: pd.DataFrame,
    runs: dict[tuple[int, int], ComboRun],
    path: Path,
    top: int = 5,
) -> Path:
    """Small multiples: equity of the top combos inside each of the windows, rebased to 1."""
    windows = sweep.window_bounds(bars.index)
    picks = [(int(r.fast), int(r.slow), r.label) for r in table.head(top).itertuples()]
    fig, axes = plt.subplots(1, len(windows), figsize=(16, 4.6), sharey=True)
    for k, (ax, w) in enumerate(zip(axes, windows, strict=True), start=1):
        hold = bars["close"].loc[w] / bars["close"].loc[w].iloc[0]
        ax.plot(hold.index, hold, color=MUTED, lw=1.1, ls="--", label="buy & hold")
        for (fast, slow, label), colour in zip(picks, SERIES, strict=False):
            eq = _equity(runs[(fast, slow)].returns.reindex(w, fill_value=0.0))
            ax.plot(eq.index, eq, color=colour, lw=1.5, label=f"{fast}/{slow} ({label})")
        ax.axhline(1.0, color=AXIS, lw=0.8)
        ax.set_title(f"W{k}: {w[0]:%d %b} → {w[-1]:%d %b}", loc="left", fontsize=10)
        ax.xaxis.set_major_locator(mdates.DayLocator(interval=2))
        _date_axis(ax)
    axes[0].set_ylabel("equity in window (×)")
    axes[-1].legend(loc="upper left", fontsize=8)
    fig.suptitle(
        f"Top {top} by full-period Sharpe, equity inside each of the {len(windows)} windows",
        x=0.01,
        ha="left",
        fontweight="bold",
        color=INK,
    )
    fig.savefig(path, dpi=130, bbox_inches="tight")
    plt.close(fig)
    return path


def walk_forward_chart(
    bars: pd.DataFrame, wf: sweep.WalkForward, screen: ComboRun, path: Path
) -> Path:
    """Out-of-sample walk-forward equity vs the in-sample 'screen' pick and buy & hold."""
    idx = wf.oos_returns.index
    oos = _equity(wf.oos_returns)
    scr = _equity(screen.returns.reindex(idx, fill_value=0.0))
    hold = bars["close"].loc[idx] / bars["close"].loc[idx].iloc[0]
    fig, ax = plt.subplots(figsize=(16, 4.6))
    ax.plot(
        oos.index,
        oos,
        color=SERIES[0],
        lw=1.8,
        label="walk-forward (picked on prior window, traded out-of-sample)",
    )
    ax.plot(
        scr.index,
        scr,
        color=SERIES[1],
        lw=1.4,
        label=f"screen #1 {screen.fast}/{screen.slow} (picked on ALL data — in-sample)",
    )
    ax.plot(hold.index, hold, color=MUTED, lw=1.1, ls="--", label="buy & hold")
    ax.axhline(1.0, color=AXIS, lw=0.8)
    for step in wf.steps.itertuples():
        start = sweep.window_bounds(bars.index)[int(step.test[1:]) - 1][0]
        ax.axvline(start, color=AXIS, lw=0.8, ls=":")
        ax.text(
            start,
            ax.get_ylim()[1],
            f" {step.test}: {step.picked}",
            va="top",
            fontsize=8,
            color=INK_2,
        )
    ax.set_ylabel("equity (×)")
    ax.legend(loc="lower left", fontsize=9)
    ax.set_title("Walk-forward over W2–W4: the honest number vs the screen", loc="left")
    _date_axis(ax)
    fig.savefig(path, dpi=130, bbox_inches="tight")
    plt.close(fig)
    return path
