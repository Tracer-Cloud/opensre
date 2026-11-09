"""ROBUSTNESS_REPORT.md: every caveat of the study, what was done about it, and the result."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from desk import config, history, sweep
from desk.history_report import robust_books
from desk.robustness import DAILY_ANN

KEY_BOOKS = (
    "ensemble always_in",
    "ensemble long_flat",
    "ensemble long_flat_gap_cd",
    "ensemble long_flat_adx",
)
SLOW_BARS = ("4h", "8h", "1d")


def _pct(x: float) -> str:
    return f"{x:+.1%}"


def _size(s: str) -> str:
    return "vol-target" if s == "vol" else ("full" if s == "raw" else s)


def _md(df: pd.DataFrame) -> str:
    head = "| " + " | ".join(df.columns) + " |\n|" + "---|" * len(df.columns) + "\n"
    return head + "\n".join("| " + " | ".join(str(v) for v in row) + " |" for row in df.values)


def _execution_section(ex: pd.DataFrame) -> str:
    sub = ex[ex["book"].isin(KEY_BOOKS) & ex["timeframe"].isin(SLOW_BARS)]
    key = ["timeframe", "book", "sizing"]
    head = sub[(sub["cost_bps"] == config.COST_BPS)].pivot_table(
        index=key, columns="execution", values=["sharpe", "passes"], aggfunc="first"
    )
    opt = sub[(sub["cost_bps"] == config.OPTIMISTIC_COST_BPS) & (sub["execution"] == "next_open")]
    opt = opt.set_index(key)["sharpe"]
    bench = sub[sub["execution"] == config.EXECUTION].drop_duplicates(key).set_index(key)
    rows = []
    for idx in head.index:
        tf, book, sizing = idx
        sh, ps = head.loc[idx, "sharpe"], head.loc[idx, "passes"]
        rows.append(
            [
                tf,
                book.replace("ensemble ", ""),
                _size(sizing),
                f"{sh['close']:.2f}{' ✓' if ps['close'] else ''}",
                f"**{sh['next_open']:.2f}{' ✓' if ps['next_open'] else ''}**",
                f"{sh['next_close']:.2f}{' ✓' if ps['next_close'] else ''}",
                f"{opt.loc[idx]:.2f}",
                f"{bench.loc[idx, 'bench_sharpe']:.2f}",
                f"{bench.loc[idx, 'max_drawdown']:.0%} / {bench.loc[idx, 'bench_max_drawdown']:.0%}",
            ]
        )
    table = pd.DataFrame(
        rows,
        columns=[
            "bars",
            "book",
            "sizing",
            f"close @{config.COST_BPS:g}",
            f"next open @{config.COST_BPS:g}",
            f"1 bar late @{config.COST_BPS:g}",
            f"next open @{config.OPTIMISTIC_COST_BPS:g}",
            "B&H Sharpe",
            "max DD / B&H",
        ],
    )
    headline = ex[(ex["cost_bps"] == config.COST_BPS) & (ex["execution"] == config.EXECUTION)]
    robust = robust_books(headline)
    robust_txt = (
        "\n".join(
            f"- {r.book} ({_size(r.sizing)}): passes on {r.passes_on}"
            + (" — **robust**" if r.robust else "")
            for r in robust.itertuples()
        )
        or "- none"
    )
    return f"""## 1. Execution test — fill at the next bar's open, pay {config.COST_BPS:g} bps

Before, every decision filled at the close of the bar that produced it. A live bot can only act
*after* that close, so the headline now fills at the **next bar's open** and pays
{config.TAKER_FEE_BPS:g} bps fee + {config.SLIPPAGE_BPS:g} bps slippage per unit of position change.
"1 bar late" fills a whole bar later (latency / missed run). Sharpe on W2–W8, ✓ = passes the
pass rule (Sharpe above *and* max drawdown shallower than the matching buy & hold).

{_md(table)}

Books passing at the headline assumptions, and on which bar sizes:

{robust_txt}
"""


def _meta_section(meta, daily: pd.DataFrame) -> str:
    oos = meta.oos_returns
    rows = [
        [
            s.train,
            s.test,
            s.picked,
            f"{s.in_sample_sharpe:.2f}",
            f"{s.oos_sharpe:.2f}",
            _pct(s.oos_return),
        ]
        for s in meta.steps.itertuples()
    ]
    bench = {
        "buy & hold": daily[("1d", "buy & hold", "raw")].loc[oos.index],
        "buy & hold vol-target": daily[("1d", "buy & hold", "vol")].loc[oos.index],
    }
    comp = [
        [
            "meta walk-forward",
            f"{sweep.sharpe(oos, DAILY_ANN):.2f}",
            _pct((1 + oos).prod() - 1),
            f"{sweep.max_drawdown(oos):.0%}",
        ]
    ]
    comp += [
        [
            k,
            f"{sweep.sharpe(v, DAILY_ANN):.2f}",
            _pct((1 + v).prod() - 1),
            f"{sweep.max_drawdown(v):.0%}",
        ]
        for k, v in bench.items()
    ]
    return f"""## 2. Picking the best row — now picked out-of-sample too

The meta walk-forward chooses, on each window, the best book among **all
{len([c for c in daily.columns if c[1] != "buy & hold"])} strategy books** (every variant ×
sizing × bar size) using only that window, and trades it on the next one. This is what you
would have earned by "picking the best row" without hindsight.

{_md(pd.DataFrame(rows, columns=["train", "test", "picked", "in-sample Sharpe", "OOS Sharpe", "OOS return"]))}

{_md(pd.DataFrame(comp, columns=["", "Sharpe", "return", "max DD"]))}
"""


def _dsr_section(dsr: pd.DataFrame, n_sets: int) -> str:
    show = dsr[dsr["book"].isin(KEY_BOOKS) & dsr["timeframe"].isin(SLOW_BARS)].copy()
    show = show.sort_values("dsr_vs_hold", ascending=False).head(12)
    rows = [
        [
            r.timeframe,
            r.book.replace("ensemble ", ""),
            _size(r.sizing),
            f"{r.sharpe:.2f}",
            f"{r.bench_sharpe:.2f}",
            f"{r.dsr:.2f}",
            f"{r.dsr_all_trials:.2f}",
            f"**{r.dsr_vs_hold:.2f}**",
            f"{r.dsr_vs_hold_all_trials:.2f}",
        ]
        for r in show.itertuples()
    ]
    sig = dsr[dsr["significant"]]
    beat = dsr[dsr["beats_hold_significantly"]]
    cross, null = dsr.attrs.get("cross_sectional_sd", float("nan")), dsr.attrs.get("null_sd", 1)
    cols = [
        "bars",
        "book",
        "sizing",
        "Sharpe",
        "B&H Sharpe",
        f"DSR ({len(dsr)} books)",
        f"DSR ({n_sets:,} sets)",
        "DSR vs B&H",
        f"DSR vs B&H ({n_sets:,} sets)",
    ]
    return f"""## 3. Deflated Sharpe Ratio — how much of it could be luck?

DSR (Bailey & López de Prado, 2014) is the probability that a Sharpe ratio is real after
accounting for how many strategies were tried: **{len(dsr)}** books, or (strictest) all
**{n_sets:,}** parameter sets of the study. "vs B&H" tests the claim actually made — a *higher
Sharpe* than buy & hold — on the book's daily return minus buy & hold scaled to the same
volatility. ≥ {config.DSR_SIGNIFICANCE:.2f} = significant. Daily returns, W2–W8.

How lucky the best of N could look is measured with the no-skill dispersion of Sharpe
estimates (√(1/T) = {null:.3f} per day). The raw spread across books ({cross:.3f} per day) is
not used: it comes from 15m books losing to costs deterministically, and with it no book could
pass at all (an earlier version of this report did that and showed 0.00 everywhere).

{_md(pd.DataFrame(rows, columns=cols))}

- Significantly positive on their own ({len(dsr)} books tried): **{len(sig)}/{len(dsr)}**.
- Sharpe significantly above buy & hold: **{len(beat)}/{len(dsr)}**.
"""


def _sensitivity_section(summary: pd.DataFrame) -> str:
    rows = [
        [
            r.book,
            r.timeframe,
            r.settings,
            f"{r.pass_rate:.0%}",
            f"{r.worst_sharpe_margin:+.2f}",
            f"{r.median_sharpe_margin:+.2f}",
            f"{r.worst_max_drawdown:.0%}",
        ]
        for r in summary.itertuples()
    ]
    return f"""## 4. Untuned thresholds — what if the textbook values were different?

Every setting is moved around its default and nothing is picked: vol target
{", ".join(f"{v:.0%}" for v in config.SENS_VOL_TARGETS)} (plus full size), gap
{", ".join(f"{g:g}" for g in config.SENS_GAP_ATR)} × ATR, cooldown
{", ".join(str(c) for c in config.SENS_COOLDOWN)} bars, ADX
{", ".join(f"{a:g}" for a in config.SENS_ADX)}. Each setting is compared with buy & hold
sized the same way (same vol target), at the headline assumptions. "Sharpe margin" = book
minus buy & hold.

{_md(pd.DataFrame(rows, columns=["book", "bars", "settings", "pass rate", "worst Sharpe margin", "median Sharpe margin", "worst max DD"]))}
"""


def _wf_zero_section(study: history.Study) -> str:
    zero = sum(int((c.wf.steps["oos_return"] == 0).sum()) for c in study.cells)
    total = sum(len(c.wf.steps) for c in study.cells)
    return f"""## 5. "Do nothing" winning a walk-forward step

The walk-forward may now only pick combos with at least {config.WF_MIN_TRADES} position changes
in the training window. Steps whose out-of-sample return is exactly 0 (nothing traded):
**{zero}/{total}**.
"""


def _assets_section(counts: pd.DataFrame, port: pd.DataFrame, per: pd.DataFrame) -> str:
    c = counts[counts["timeframe"].isin(SLOW_BARS)]
    rows = [
        [r.book.replace("ensemble ", ""), _size(r.sizing), r.timeframe, f"{r.passes}/{r.coins}"]
        for r in c.itertuples()
    ]
    p = port.copy()
    prow = [
        [
            r.timeframe,
            r.book.replace("ensemble ", ""),
            _size(r.sizing),
            f"{r.sharpe:.2f}",
            _pct(r.cagr),
            f"{r.max_drawdown:.0%}",
            f"{r.positive_years}/{r.years}",
            "**YES**" if r.passes else ("bench" if r.book == "buy & hold" else "no"),
        ]
        for r in p.itertuples()
    ]
    coins = ", ".join(sorted(per["symbol"].unique()))
    return f"""## 6. One asset, one bullish sample → BTC since 2017 and {per["symbol"].nunique()} coins

BTC history now starts in Aug 2017 (includes the 2018 −84% bear market and the 2022 crash).
The **same untuned books** (no re-fitting per coin) run on {coins}, each against its own buy &
hold on its own W2–W8, at the headline assumptions.

Coins passed, per book and bar size:

{_md(pd.DataFrame(rows, columns=["book", "sizing", "bars", "coins passed"]))}

Equal-weight portfolio (each coin enters after a {120}-day warm-up; daily rebalanced; the
benchmark is the equal-weight buy & hold portfolio sized the same way):

{_md(pd.DataFrame(prow, columns=["bars", "book", "sizing", "Sharpe", "CAGR", "max DD", "years > 0", "passes"]))}

Survivorship bias: coins delisted since 2018 cannot be downloaded, so the basket is made of
survivors. That flatters buy & hold more than trend following (which exits collapsing coins),
but it still means the coin list was chosen with hindsight.
"""


def _paper_section(status: str) -> str:
    books = "\n".join(f"- {s} {tf} `{v}` ({_size(z)})" for s, tf, v, z in config.PAPER_BOOKS)
    return f"""## 7. Paper trading — the only test hindsight cannot touch

Pre-registered (chosen before the checks above were run):

{books}

`run_paper.py` appends every decision to an append-only ledger at the moment it is made and
prices it like the study ({config.COST_LABEL}). Run it 24/7 on your machine:

```bash
cd quant_desk && nohup .venv/bin/python run_paper.py --loop >> output/paper/paper.log 2>&1 &
# or cron, every 4 hours:  2 */4 * * *  cd /path/to/quant_desk && .venv/bin/python run_paper.py
```

Current status (`output/paper/STATUS.md`):

{status.split(chr(10), 2)[2] if status else "_not started_"}
"""


def write(
    study: history.Study,
    ex: pd.DataFrame,
    meta,
    daily: pd.DataFrame,
    dsr: pd.DataFrame,
    n_sets: int,
    sens_summary: pd.DataFrame,
    counts: pd.DataFrame,
    port: pd.DataFrame,
    per: pd.DataFrame,
    paper_status: str,
    path: Path,
) -> Path:
    start = study.bars["15m"].index[0]
    text = f"""# Robustness report — every caveat, tested

Headline assumptions everywhere: **{config.COST_LABEL}**. BTC/USDT history from
{start:%Y-%m-%d}. Pass rule (fixed in advance): on W2–W8, Sharpe above *and* max drawdown
shallower than buy & hold sized the same way. Details per sweep: `HISTORY_REPORT.md`.

{_execution_section(ex)}
{_meta_section(meta, daily)}
{_dsr_section(dsr, n_sets)}
{_sensitivity_section(sens_summary)}
{_wf_zero_section(study)}
{_assets_section(counts, port, per)}
{_paper_section(paper_status)}
## What is still not solved

- Long/flat was added after seeing that shorting BTC lost money: hindsight that no backtest can
  remove. Paper trading is the fix, and it needs months.
- Binance only: other exchanges are blocked by this environment's network policy.
- Survivorship in the coin list (above).
"""
    path.write_text(text)
    return path
