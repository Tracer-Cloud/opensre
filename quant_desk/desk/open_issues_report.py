"""Report sections for the three issues left open: long-only hindsight, survivorship, venues."""

from __future__ import annotations

import pandas as pd

from desk import config, data, universe


def _pct(x: float) -> str:
    return f"{x:+.1%}"


def _size(s: str) -> str:
    return "vol-target" if s == "vol" else "full"


def _md(df: pd.DataFrame) -> str:
    head = "| " + " | ".join(map(str, df.columns)) + " |\n|" + "---|" * len(df.columns) + "\n"
    return head + "\n".join("| " + " | ".join(str(v) for v in row) + " |" for row in df.values)


def long_short_section(table: pd.DataFrame, verdict: pd.DataFrame, univ: pd.DataFrame) -> str:
    v = pd.DataFrame(
        [
            [
                r.group + (" ⚠ decision data" if r.decision_data else ""),
                r.comparisons,
                f"{r.long_flat_better:.0%}",
                f"{r.short_side_loses:.0%}",
                f"{r.median_short_side_sharpe:+.2f}",
                _pct(r.median_short_side_per_year),
            ]
            for r in verdict.itertuples()
        ],
        columns=[
            "data",
            "comparisons",
            "long/flat Sharpe ≥ long/short",
            "short side loses money",
            "median short-side Sharpe",
            "median short-side return / yr",
        ],
    )
    btc = table[table["dataset"].str.startswith("BTC") & ~table["decision_data"]]
    btc = btc[btc["sizing"] == "vol"]
    d = pd.DataFrame(
        [
            [
                r.dataset,
                r.timeframe,
                r.pair,
                f"{r.sharpe_long_short:.2f}",
                f"{r.sharpe_long_flat:.2f}",
                f"{r.short_side_sharpe:+.2f}",
                _pct(r.short_side_per_year),
            ]
            for r in btc.itertuples()
        ],
        columns=[
            "data",
            "bars",
            "pair",
            "long/short",
            "long/flat",
            "short side Sharpe",
            "short side / yr",
        ],
    )
    u = univ[(univ["universe"] == "point-in-time") & (univ["sizing"] == "vol")].set_index(
        ["timeframe", "book"]
    )
    urows = []
    for ls, lf in config.LONG_SHORT_PAIRS:
        for tf in config.UNIVERSE_TIMEFRAMES:
            a, b = u.loc[(tf, ls)], u.loc[(tf, lf)]
            urows.append(
                [
                    tf,
                    f"{ls} → {lf}",
                    f"{a.sharpe:.2f}",
                    f"{b.sharpe:.2f}",
                    f"{a.max_drawdown:.0%}",
                    f"{b.max_drawdown:.0%}",
                ]
            )
    unseen = table[~table["decision_data"]]
    share = unseen["long_flat_better"].mean()
    loses = unseen["short_side_loses"].mean()
    return f"""## 8. Long-only was chosen in hindsight — tested on data the decision never saw

The long/flat variants were added after looking at BTC on Binance from
{config.DECISION_DATA_START}. Each long/short variant is paired with its long/flat twin, and the
**short side's contribution** (long/short minus long/flat, with its own costs) is scored on data
that played no part in that decision: BTC on Bitstamp 2013–2017, BTC on Binance 2017–2021, eight
other coins, and the survivorship-free universe (which includes coins that collapsed — exactly
where shorting should shine).

{_md(v)}

On never-seen data, long/flat had a Sharpe at least as high in **{share:.0%}** of
{len(unseen)} comparisons and the short side lost money in **{loses:.0%}**.

BTC hold-outs in detail (vol-targeted):

{_md(d)}

Survivorship-free universe (vol-targeted), long/short vs long/flat:

{_md(pd.DataFrame(urows, columns=["bars", "pair", "Sharpe long/short", "Sharpe long/flat", "max DD long/short", "max DD long/flat"]))}
"""


def survivorship_section(univ: pd.DataFrame, table: pd.DataFrame, casualties: pd.DataFrame) -> str:
    status = data.load(universe.SYMBOL_STATUS)
    n_sym, n_break = len(status), int((status["status"] == "BREAK").sum())
    members = table["instrument"].unique()
    ended = casualties["instrument"].nunique() if not casualties.empty else 0
    books_shown = ("buy & hold", "long_flat", "long_flat_gap_cd", "long_flat_adx")
    p = univ[univ["book"].isin(books_shown)]
    rows = []
    for (tf, book, sizing), g in p.groupby(["timeframe", "book", "sizing"], sort=False):
        pit = g[g["universe"] == "point-in-time"].iloc[0]
        sur = g[g["universe"] == "survivors only"].iloc[0]
        rows.append(
            [
                tf,
                book,
                _size(sizing),
                f"{pit.sharpe:.2f}",
                _pct(pit.cagr),
                f"{pit.max_drawdown:.0%}",
                "bench" if book == "buy & hold" else ("**YES**" if pit.passes else "no"),
                f"{sur.sharpe:.2f}",
                _pct(sur.cagr),
            ]
        )
    cas = casualties.head(12)
    book_col = [c for c in cas.columns if c.endswith("_while_member")][-1] if not cas.empty else ""
    crow = [
        [
            r["instrument"],
            r["status"],
            r["months_in_universe"],
            f"{r['last_bar']:%Y-%m-%d}",
            _pct(r["buy_and_hold_while_member"]),
            _pct(r[book_col]),
        ]
        for _, r in cas.iterrows()
    ]
    return f"""## 9. Survivorship bias — a point-in-time universe with the dead coins put back

Binance still serves the history of delisted pairs: **{n_sym}** risk-asset USDT pairs were
downloaded, **{n_break}** of them delisted. Every month the universe is the top
**{config.UNIVERSE_SIZE}** by trailing {config.UNIVERSE_VOLUME_DAYS}-day USDT volume, chosen only
from data before that month (≥ {config.UNIVERSE_MIN_HISTORY_DAYS} days of history, still trading).
**{len(members)}** instruments were members at some point; **{ended}** of them were later
delisted or stopped trading. A ticker re-used after a halt (old LUNA → new LUNA) is split, never
stitched. Each member gets 1/{config.UNIVERSE_SIZE} of capital; a coin whose data stops exits at
its last price with costs. Benchmark: equal-weight buy & hold of the same monthly members.
"Survivors only" rebuilds the universe from coins still listed today — the gap between the two
is the bias the earlier 9-coin test carried.

{_md(pd.DataFrame(rows, columns=["bars", "book", "sizing", "Sharpe", "CAGR", "max DD", "passes", "survivors-only Sharpe", "survivors-only CAGR"]))}

Members that later died or were delisted (8h bars), while they were members:

{_md(pd.DataFrame(crow, columns=["instrument", "status", "months in universe", "last bar", "buy & hold", book_col.replace("_while_member", "")]))}
"""


def venue_section(cmp: pd.DataFrame, corr: pd.DataFrame) -> str:
    common = cmp[cmp["period"].str.startswith("common")]
    rows = []
    for (tf, book, sizing), g in common.groupby(["timeframe", "book", "sizing"], sort=False):
        cells = []
        for venue in config.VENUES:
            r = g[g["venue"] == venue]
            if r.empty:
                cells.append("–")
                continue
            r = r.iloc[0]
            mark = "" if book == "buy & hold" else (" ✓" if r.passes else "")
            cells.append(f"{r.sharpe:.2f}{mark}")
        rows.append([tf, book, _size(sizing), *cells])
    pre = cmp[cmp["period"].str.startswith("Bitstamp")]
    prow = [
        [
            r.timeframe,
            r.book,
            _size(r.sizing),
            f"{r.sharpe:.2f}",
            _pct(r.cagr),
            f"{r.max_drawdown:.0%}",
            "bench" if r.book == "buy & hold" else ("**YES**" if r.passes else "no"),
        ]
        for r in pre.itertuples()
    ]
    passed = pre[(pre["book"] != "buy & hold") & pre["passes"]]
    corr_txt = _md(corr.round(3).reset_index().rename(columns={"index": ""}))
    return f"""## 10. Binance-only — the same BTC books on Bitstamp and Coinbase

Bitstamp (BTC/USD since 2013) and Coinbase (BTC-USD since 2015) were added. Same books, same
costs and execution, each venue against its own buy & hold. Sharpe from 2018 (✓ = passes):

{_md(pd.DataFrame(rows, columns=["bars", "book", "sizing", *config.VENUES]))}

Correlation of the venues' daily returns (long_flat_gap_cd, 4h, vol-targeted):

{corr_txt}

**Bitstamp 2013-01 → 2017-08** — BTC history that no decision in this project ever looked at
(includes the 2014 crash). Passes: **{len(passed)}/{len(pre[pre["book"] != "buy & hold"])}**.

{_md(pd.DataFrame(prow, columns=["bars", "book", "sizing", "Sharpe", "CAGR", "max DD", "passes"]))}
"""


def still_open(ls_table: pd.DataFrame, univ: pd.DataFrame, cmp: pd.DataFrame) -> str:
    unseen = ls_table[~ls_table["decision_data"]]
    lf_share = unseen["long_flat_better"].mean()
    pit = univ[
        (univ["universe"] == "point-in-time") & univ["book"].isin(("long_flat", "long_flat_gap_cd"))
    ]
    pre = cmp[cmp["period"].str.startswith("Bitstamp") & (cmp["book"] != "buy & hold")]
    return f"""## What is still open

- **Long-only hindsight**: on never-seen data long/flat matched or beat long/short in
  {lf_share:.0%} of comparisons. That tests the decision; it cannot make it un-seen. Paper
  trading remains the only clean test.
- **Survivorship**: solved for every coin Binance ever listed ({int(pit["passes"].sum())}/{len(pit)}
  long/flat books pass on the point-in-time universe). Coins never listed on Binance are not
  covered.
- **Venues**: BTC is now checked on Bitstamp and Coinbase ({int(pre["passes"].sum())}/{len(pre)}
  books pass on Bitstamp 2013–2017). Altcoins remain Binance-only.
- Still one asset class (crypto), and all of it is history. Run `run_paper.py --loop`.
"""
