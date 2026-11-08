"""Markdown report of the multi-year study."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from desk import config, history, sweep, variants
from desk.history import Study


def _pct(x: float) -> str:
    return f"{x:+.1%}"


def _num(x: float) -> str:
    return "–" if pd.isna(x) else f"{x:.2f}"


def _summary_table(s: pd.DataFrame) -> str:
    head = (
        "| bars | variant | combos > 0 | plateaus (2 bps) | plateaus (10 bps) | best plateau | "
        "its Sharpe 2 / 10 bps | trades/yr | time in market | WF OOS Sharpe 2 / 10 bps | "
        "WF steps > 0 | WF OOS return / max DD | B&H OOS Sharpe | B&H OOS return / max DD |\n"
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|\n"
    )
    lines = [
        f"| {r.timeframe} | {r.variant} | {r.positive}/{r.combos} | **{r.plateaus}** | "
        f"{r.plateaus_10bps} | {r.best_plateau} | {_num(r.best_plateau_sharpe)} / "
        f"{_num(r.best_plateau_sharpe_10bps)} | {r.median_trades_per_year:.0f} | "
        f"{r.median_exposure:.0%} | {r.wf_oos_sharpe:.2f} / {r.wf_oos_sharpe_10bps:.2f} | "
        f"{r.wf_steps_positive}/{r.wf_steps} | {_pct(r.wf_oos_return)} / "
        f"{r.wf_oos_max_drawdown:.0%} | {r.hold_oos_sharpe:.2f} | {_pct(r.hold_oos_return)} / "
        f"{r.hold_oos_max_drawdown:.0%} |"
        for r in s.itertuples()
    ]
    return head + "\n".join(lines)


def _plateau_section(study: Study) -> str:
    parts = []
    for cell in study.cells:
        t = cell.table
        plateaus = t[t["label"] == "plateau"]
        if plateaus.empty:
            continue
        a, b = cell.variant.axes
        rows = "\n".join(
            f"| {r[a]:g}/{r[b]:g} | {r['sharpe']:.2f} | {r['sharpe_10bps']:.2f} | "
            f"{_pct(r['total_return'])} | {r['max_drawdown']:.0%} | "
            f"{r['positive_windows']}/{config.HISTORY_WINDOWS} | "
            f"{r['neighbour_median_sharpe']:.2f} | {r['exposure']:.0%} |"
            for _, r in plateaus.head(5).iterrows()
        )
        parts.append(
            f"### {cell.timeframe} · {cell.variant.name} — {len(plateaus)} plateaus "
            f"(top 5)\n\n| {a}/{b} | Sharpe 2 bps | Sharpe 10 bps | return | max DD | "
            "windows > 0 | neighbours' median Sharpe | time in market |\n"
            "|---|---|---|---|---|---|---|---|\n" + rows
        )
    return "\n\n".join(parts) if parts else "_No plateau anywhere._"


def _wf_steps(study: Study, summary: pd.DataFrame, top: int = 3) -> str:
    best = summary.sort_values("wf_oos_sharpe", ascending=False).head(top)
    parts = []
    for r in best.itertuples():
        cell = next(
            c for c in study.cells if c.timeframe == r.timeframe and c.variant.name == r.variant
        )
        rows = "\n".join(
            f"| {s.train} → {s.test} | {s.picked} | {s.in_sample_sharpe:.2f} | "
            f"{s.oos_sharpe:.2f} | {_pct(s.oos_return)} |"
            for s in cell.wf.steps.itertuples()
        )
        parts.append(
            f"#### {r.timeframe} · {r.variant}\n\n| train → test | picked | in-sample Sharpe | "
            "OOS Sharpe | OOS return |\n|---|---|---|---|---|\n" + rows
        )
    return "\n\n".join(parts)


def _windows(study: Study) -> str:
    tf = next(iter(study.bars))
    idx = study.bars[tf].index
    close = study.bars[tf]["close"]
    rows = []
    for i, w in enumerate(sweep.window_bounds(idx, config.HISTORY_WINDOWS), start=1):
        c = close.loc[w]
        rows.append(
            f"| W{i} | {w[0]:%Y-%m-%d} → {w[-1]:%Y-%m-%d} | {c.iloc[0]:,.0f} → "
            f"{c.iloc[-1]:,.0f} | {_pct(c.iloc[-1] / c.iloc[0] - 1)} |"
        )
    return "| window | dates | BTC | move |\n|---|---|---|---|\n" + "\n".join(rows)


def _candidates_table(c: pd.DataFrame) -> str:
    head = (
        "| bars | book | sizing | Sharpe | vs bench | CAGR | max DD | vs bench | "
        "windows > 0 | avg exposure | turnover/yr | passes |\n"
        "|---|---|---|---|---|---|---|---|---|---|---|---|\n"
    )
    lines = [
        f"| {r.timeframe} | {r.book} | {'vol-target' if r.sizing == 'vol' else 'full'} | "
        f"{r.sharpe:.2f} | {r.bench_sharpe:.2f} | {_pct(r.cagr)} | {r.max_drawdown:.0%} | "
        f"{r.bench_max_drawdown:.0%} | {r.positive_windows}/{config.HISTORY_WINDOWS - 1} | "
        f"{r.avg_exposure:.0%} | {r.turnover_per_year:.0f}× | "
        f"{'**YES**' if r.passes else ('bench' if r.book == 'buy & hold' else 'no')} |"
        for r in c.itertuples()
    ]
    return head + "\n".join(lines)


REPORTED_BOOKS = (
    "buy & hold",
    "ensemble always_in",
    "ensemble long_flat",
    "ensemble long_flat_gap_cd",
    "ensemble long_flat_adx",
)


def robust_books(stress: pd.DataFrame) -> pd.DataFrame:
    """Books that pass at >= 2 *adjacent* bar sizes (a lucky single bar size does not count)."""
    order = {tf: i for i, tf in enumerate(config.TIMEFRAMES)}
    rows = []
    for (book, sizing), g in stress[stress["passes"]].groupby(["book", "sizing"]):
        idx = sorted(order[tf] for tf in g["timeframe"])
        best_run, run = 1, 1
        for a, b in zip(idx, idx[1:], strict=False):
            run = run + 1 if b == a + 1 else 1
            best_run = max(best_run, run)
        rows.append(
            {
                "book": book,
                "sizing": sizing,
                "passes_on": ", ".join(sorted(g["timeframe"], key=order.get)),
                "adjacent": best_run,
                "robust": best_run >= 2,
            }
        )
    return pd.DataFrame(rows, columns=["book", "sizing", "passes_on", "adjacent", "robust"])


def _robust_table(r: pd.DataFrame) -> str:
    if r.empty:
        return "_No book passes anywhere._"
    lines = [
        f"| {x.book} | {'vol-target' if x.sizing == 'vol' else 'full'} | {x.passes_on} | "
        f"{'**robust**' if x.robust else 'single bar size only'} |"
        for x in r.sort_values(["robust", "adjacent"], ascending=False).itertuples()
    ]
    return "| book | sizing | passes on | verdict |\n|---|---|---|---|\n" + "\n".join(lines)


def _robust_section(cand: dict[float, pd.DataFrame]) -> str:
    stress = cand[history.STRESS_FEE_BPS]
    cheap = cand[config.FEE_BPS]
    passed = stress[stress["passes"]]
    passed_cheap = cheap[cheap["passes"]]
    verdict = (
        ", ".join(
            f"{r.timeframe} {r.book} ({'vol-target' if r.sizing == 'vol' else 'full'}): "
            f"Sharpe {r.sharpe:.2f} vs {r.bench_sharpe:.2f}, max DD {r.max_drawdown:.0%} vs "
            f"{r.bench_max_drawdown:.0%}"
            for r in passed.itertuples()
        )
        or "nothing"
    )
    return f"""## Robust construction — no parameter is picked anywhere

Instead of choosing one combo, each trend variant trades its **whole grid at once**: exposure is
the average position of all 212 combos, netted into one book (fees on the net change). Each book
is shown at full size and **vol-targeted** (exposure × min(1, {config.VOL_TARGET:.0%} /
{config.VOL_WINDOW_DAYS}-day realised vol), re-set daily, no leverage). Buy & hold gets the same
vol-targeting, so every candidate is compared against the benchmark sized the same way.

**Pass rule (fixed before looking at the results):** on W2–W8 at {history.STRESS_FEE_BPS:g} bps,
Sharpe above *and* max drawdown shallower than the matching buy & hold.
**Robust** = passes on at least two *adjacent* bar sizes
({" → ".join(config.TIMEFRAMES)}), so one lucky bar size is not enough.

**Passes at {history.STRESS_FEE_BPS:g} bps ({len(passed)}):** {verdict}.
At {config.FEE_BPS:g} bps: {len(passed_cheap)} pass.

{_robust_table(robust_books(stress))}

Main books at {history.STRESS_FEE_BPS:g} bps (all {stress["book"].nunique() - 1} variants in
`candidates.csv`):

{_candidates_table(stress[stress["book"].isin(REPORTED_BOOKS)])}

The choice of *which* variant to trade is still made after seeing this table; with
{stress["book"].nunique() - 1} variants × 2 sizings × {len(config.TIMEFRAMES)} bar sizes, expect
a few to pass by luck. The long/flat variants were also added *after* seeing that shorting
BTC lost money, which is hindsight. The only fully clean test left is new data: paper trade.
"""


def write(study: Study, summary: pd.DataFrame, cand: dict[float, pd.DataFrame], path: Path) -> Path:
    any_tf = next(iter(study.bars))
    start, end = study.bars[any_tf].index[0], study.bars[any_tf].index[-1]
    found = summary[summary["plateaus"] > 0]
    survivors = summary[summary["plateaus_10bps"] > 0]
    beat_hold = summary[summary["wf_oos_sharpe"] > summary["hold_oos_sharpe"]]
    variant_lines = "\n".join(f"- `{v.name}` — {v.description}" for v in variants.VARIANTS)

    text = f"""# Multi-year study — does any plateau show up?

BTC/USDT, Binance 15m klines {start:%Y-%m-%d} → {end:%Y-%m-%d} ({len(study.bars["15m"]):,} bars),
resampled to 1h and 4h (bars stamped at close). Every variant is swept over its whole grid on
every bar size: {len(summary)} sweeps, {int(summary["combos"].sum()):,} parameter sets.
Fees {config.FEE_BPS:g} bps per unit of position change, stress-tested at
{history.STRESS_FEE_BPS:g} bps (Binance standard taker). History split into
{config.HISTORY_WINDOWS} equal windows.

**Plateau** = grid neighbours keep ≥ {config.PLATEAU_NEIGHBOUR_RATIO:.0%} of its Sharpe *and*
profitable in ≥ {history.min_positive_windows()}/{config.HISTORY_WINDOWS} windows.
**Walk-forward (WF)**: on each window pick the variant's best combo, trade it on the next
window; W2–W8 stitched = {config.HISTORY_WINDOWS - 1} out-of-sample steps.

## Variants

{variant_lines}

Regime filters use textbook settings (ER {config.ER_WINDOW} bars ≥ {config.ER_TREND},
ADX {config.ADX_WINDOW} ≥ {config.ADX_TREND:g}, gap {config.MIN_GAP_ATR} × ATR
{config.ATR_WINDOW}, cooldown {config.COOLDOWN_BARS} bars). They were **not tuned**.

## Windows

{_windows(study)}

## Answer

- Plateaus at {config.FEE_BPS:g} bps in **{len(found)}/{len(summary)}** sweeps:
  {", ".join(f"{r.timeframe} {r.variant} ({r.plateaus})" for r in found.itertuples()) or "none"}.
- Still plateaus at {history.STRESS_FEE_BPS:g} bps in **{len(survivors)}** sweeps:
  {", ".join(f"{r.timeframe} {r.variant} ({r.plateaus_10bps})" for r in survivors.itertuples()) or "none"}.
- Walk-forward Sharpe above buy & hold over the same span in **{len(beat_hold)}** sweeps:
  {", ".join(f"{r.timeframe} {r.variant} ({r.wf_oos_sharpe:.2f} vs {r.hold_oos_sharpe:.2f})" for r in beat_hold.itertuples()) or "none"}.

## Summary

{_summary_table(summary)}

`trades/yr` and `time in market` are medians over the grid. Full per-combo tables are in
`sweeps/` and in ArcticDB (`{config.SYMBOL}_<bars>_<variant>_history_sweep`).

{_robust_section(cand)}

## Plateaus

{_plateau_section(study)}

## Walk-forward detail (top 3 by OOS Sharpe)

{_wf_steps(study, summary)}

## Read before trusting

- Picking the best row of the summary table is itself in-sample selection across
  {len(summary)} sweeps. The walk-forward numbers are the honest ones, and only per variant.
- The regime filters and thresholds are textbook values, not tuned. Tuning them would add yet
  another layer of overfitting.
- A walk-forward Sharpe of exactly 0.00 means every window picked a setting that never
  traded in the next window — at high fees "do nothing" was the best in-sample choice.
- 2 bps assumes VIP or maker fees. If a result needs 2 bps to work, it does not work for
  most accounts.
- One asset, one exchange, one mostly-bullish 5-year history. Check other assets and paper
  trade before risking money.

## Charts

- `wf_sharpe.png` — out-of-sample walk-forward Sharpe per variant and bar size, 2 vs 10 bps
- `heatmaps_trend.png` / `heatmaps_mean_reversion.png` — Sharpe over every grid, plateaus dotted
- `wf_equity.png` — out-of-sample equity per variant vs buy & hold (log scale)
- `candidates.png` — whole-grid ensembles (full size and vol-targeted) vs buy & hold, 10 bps
"""
    path.write_text(text)
    return path
