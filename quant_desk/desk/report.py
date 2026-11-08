"""Markdown report of a desk run: data, indicators, ranking, plateau/peak, walk-forward."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from desk import config, sweep
from desk.strategy import ComboRun


def _pct(x: float) -> str:
    return f"{x:+.2%}"


def _ranking_table(table: pd.DataFrame, n: int) -> str:
    head = (
        "| # | fast/slow | Sharpe | return | max DD | trades | W1 | W2 | W3 | W4 | "
        "neighbours' median Sharpe | label |\n"
        "|---|---|---|---|---|---|---|---|---|---|---|---|\n"
    )
    lines = [
        f"| {r.rank} | {r.fast}/{r.slow} | {r.sharpe:.2f} | {_pct(r.total_return)} | "
        f"{r.max_drawdown:.1%} | {r.trades} | {_pct(r.w1_return)} | {_pct(r.w2_return)} | "
        f"{_pct(r.w3_return)} | {_pct(r.w4_return)} | {r.neighbour_median_sharpe:.2f} | "
        f"**{r.label}** |"
        for r in table.head(n).itertuples()
    ]
    return head + "\n".join(lines)


def write(
    bars: pd.DataFrame,
    table: pd.DataFrame,
    best: ComboRun,
    ind: pd.DataFrame,
    profile: pd.DataFrame,
    wf: sweep.WalkForward,
    path: Path,
) -> Path:
    counts = table["label"].value_counts()
    hold = float(bars["close"].iloc[-1] / bars["close"].iloc[0] - 1.0)
    top = table.iloc[0]
    oos = wf.oos_returns
    oos_ret = float((1.0 + oos).prod() - 1.0)
    hold_oos = float(bars["close"].loc[oos.index[-1]] / bars["close"].loc[oos.index[0]] - 1.0)
    daily = (1.0 + best.returns).groupby(best.returns.index.date).prod() - 1.0
    best_day, best_day_ret = daily.idxmax(), float(daily.max())
    ex_best = float((1.0 + daily.drop(best_day)).prod() - 1.0)
    store_path = config.DATA_DIR.relative_to(config.ROOT) / "arctic"
    screen_oos = best.returns.reindex(oos.index, fill_value=0.0)
    poc = profile[profile["poc"]].iloc[0]
    va = profile[profile["value_area"]]
    last = ind.iloc[-1]

    plateaus = table[table["label"] == "plateau"]
    peaks = table[table["label"] == "peak"]

    def _listing(df: pd.DataFrame) -> str:
        if df.empty:
            return "_none_"
        return "\n".join(
            f"- `{r.fast}/{r.slow}` Sharpe {r.sharpe:.2f} — {r.why}" for r in df.itertuples()
        )

    wf_rows = "\n".join(
        f"| {s.train} → {s.test} | {s.picked} | {s.in_sample_sharpe:.2f} | {s.oos_sharpe:.2f} | "
        f"{_pct(s.oos_return)} |"
        for s in wf.steps.itertuples()
    )

    text = f"""# Quant desk run — {config.SYMBOL} 15m EMA crossover

Data: Binance public klines, {len(bars)} × 15m bars ({bars.index[0]:%Y-%m-%d %H:%M} →
{bars.index[-1]:%Y-%m-%d %H:%M} UTC, bars stamped at close), stored in ArcticDB
(`lmdb://{store_path}`, symbols `{config.SYMBOL_RAW}`, `{config.SYMBOL_BARS}`, `{config.SYMBOL_SWEEP}`).
Buy & hold over the period: **{_pct(hold)}**.

Fees: {config.FEE_BPS:g} bps per unit of position change (entry 2 bps, long↔short flip 4 bps).
Sharpe is annualised with √(365 × 96) on 15m returns, net of fees.

## Indicators at the last bar (gs-quant timeseries)

| close | EMA {best.fast} | EMA {best.slow} | Bollinger 20/2σ | RSI 14 |
|---|---|---|---|---|
| {last.close:,.2f} | {last[f"ema_{best.fast}"]:,.2f} | {last[f"ema_{best.slow}"]:,.2f} | {last.bb_lower:,.2f} – {last.bb_upper:,.2f} | {last.rsi:.1f} |

Volume profile (30 days, {config.VOLUME_PROFILE_BINS} buckets): point of control
**{poc.price_low:,.0f} – {poc.price_high:,.0f}**, 70% value area
{va.price_low.min():,.0f} – {va.price_high.max():,.0f}.

## Sweep: {len(table)} combos ranked by Sharpe

fast {config.FAST_SPANS[0]}–{config.FAST_SPANS[-1]} (step 2) × slow {config.SLOW_SPANS[0]}–{config.SLOW_SPANS[-1]}
(step 4), slow ≥ 1.5 × fast. W1–W4 = return inside each quarter of the 30 days.

{_ranking_table(table, config.TOP_N_LABELLED)}

Full table: `sweep.csv`. Labels across all combos: {counts.get("plateau", 0)} plateau,
{counts.get("peak", 0)} peak, {counts.get("loser", 0)} loser (Sharpe ≤ 0).

**Plateau** = grid neighbours (±1 step) keep ≥ {config.PLATEAU_NEIGHBOUR_RATIO:.0%} of its
Sharpe *and* it is positive in ≥ {config.PLATEAU_MIN_POSITIVE_WINDOWS}/{config.N_WINDOWS} windows.
**Peak** = a good number that falls apart one step away or comes from one window.

### Plateaus
{_listing(plateaus)}

### Peaks
{_listing(peaks)}

## Shown on the chart: #1 `{best.fast}/{best.slow}`

Sharpe {top.sharpe:.2f}, return {_pct(top.total_return)}, max drawdown {top.max_drawdown:.1%},
{len(best.fills)} trades, fees paid {best.fills["fee"].sum():.2%} of equity.
Its best day ({best_day:%d %b}) made {_pct(best_day_ret)}; without that one day the 30 days
return {_pct(ex_best)}.
Every LONG/SHORT in `trades.png` / `trades_zoom.png` sits on the bar whose close fired it, and
`agent.log` has one line per trade stamped with that same bar.

> This combo was picked on the same data it is shown on. It is a screen, not proof.

## Walk-forward (pick on window k, trade window k+1)

| train → test | picked | in-sample Sharpe | out-of-sample Sharpe | out-of-sample return |
|---|---|---|---|---|
{wf_rows}

Stitched out-of-sample W2–W4: **{_pct(oos_ret)}** (Sharpe {sweep.sharpe(oos):.2f}) vs buy & hold
{_pct(hold_oos)} and the in-sample screen pick {_pct(float((1 + screen_oos).prod() - 1))} over the
same span. 30 days gives only 3 out-of-sample steps — treat this as a smoke test, not evidence.

## Charts

- `trades.png` — price, EMAs, Bollinger, every fill, RSI, equity vs buy & hold, volume profile
- `trades_zoom.png` — last 2 days as candles with each trade labelled with its bar time
- `sweep_heatmap.png` — Sharpe over the whole grid, peaks and plateaus marked
- `windows.png` — top 5 combos' equity inside each window
- `walk_forward.png` — out-of-sample walk-forward vs the screen and buy & hold
"""
    path.write_text(text)
    return path
