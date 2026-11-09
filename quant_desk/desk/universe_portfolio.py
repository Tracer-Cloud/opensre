"""Survivorship-free portfolio: the same untuned books on the point-in-time universe.

Each month the top-``UNIVERSE_SIZE`` coins (chosen from data before that month, delisted coins
included) get 1/N of capital each; a coin's sleeve trades the book only while the coin is a
member. A coin whose data stops (delisting, collapse) exits at its last price with costs and
leaves its slice in cash until the next month. The benchmark is equal-weight buy & hold of the
*same* monthly universe. A "survivors only" universe -- built the same way but only from coins
still trading today -- measures how much the old survivor basket flattered the results.
"""

from __future__ import annotations

import pandas as pd

from desk import books, config, data, sweep, universe
from desk.robustness import DAILY_ANN, to_daily

BOOKS = ("buy & hold", *books.ALL_BOOKS)


def member_mask(table: pd.DataFrame, instrument: str, index: pd.DatetimeIndex) -> pd.Series:
    """1 on bars whose close falls in a month the instrument was a member (month start
    excluded: the choice is made with data up to that moment)."""
    months = pd.to_datetime(table.loc[table["instrument"] == instrument, "month"], utc=True)
    mask = pd.Series(0.0, index=index)
    for m in months:
        nxt = m + pd.offsets.MonthBegin(1)
        mask[(index > m) & (index <= nxt)] = 1.0
    return mask


def sleeve_returns(bars: pd.DataFrame, exposure: pd.Series, mask: pd.Series, n: int) -> pd.Series:
    """Capital-weighted returns of one coin's sleeve, closing out if the data ends early."""
    x = (exposure * mask / n).fillna(0.0)
    r = books.returns(bars, x)
    if (
        len(x)
        and x.iloc[-1] != 0
        and bars.index[-1] < pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=2)
    ):
        r.iloc[-1] -= config.COST_BPS * 1e-4 * abs(x.iloc[-1])  # forced exit at the last price
    return r


def portfolio_daily(
    expos: dict[tuple[str, str], tuple[pd.DataFrame, dict[str, pd.Series]]],
    table: pd.DataFrame,
    timeframe: str,
    book: str,
    sizing: str,
) -> pd.Series:
    parts = []
    for inst in table["instrument"].unique():
        key = (universe.arctic_symbol(inst), timeframe)
        if key not in expos:
            continue
        bars, exp = expos[key]
        if book != "buy & hold" and book not in exp:
            continue
        base = pd.Series(1.0, index=bars.index) if book == "buy & hold" else exp[book]
        exposure = books.sized(bars, base, sizing, timeframe)
        mask = member_mask(table, inst, bars.index)
        parts.append(to_daily(sleeve_returns(bars, exposure, mask, config.UNIVERSE_SIZE)))
    frame = pd.concat(parts, axis=1).fillna(0.0)
    daily = frame.sum(axis=1)
    return daily.loc[daily.index > table["month"].min()]


def evaluate(
    expos: dict[tuple[str, str], tuple[pd.DataFrame, dict[str, pd.Series]]],
    tables: dict[str, pd.DataFrame],
) -> tuple[pd.DataFrame, dict[tuple[str, str, str, str], pd.Series]]:
    rows, curves = [], {}
    for label, table in tables.items():
        for tf in config.UNIVERSE_TIMEFRAMES:
            for book in BOOKS:
                for sizing in ("raw", "vol"):
                    daily = portfolio_daily(expos, table, tf, book, sizing)
                    span = daily.index[
                        sweep.window_slices(len(daily), config.HISTORY_WINDOWS)[1].start :
                    ]
                    r = daily.loc[span]
                    curves[(label, tf, book, sizing)] = r
                    rows.append(
                        {
                            "universe": label,
                            "timeframe": tf,
                            "book": book,
                            "sizing": sizing,
                            "start": span[0],
                            **books.score(r, DAILY_ANN),
                        }
                    )
    out = pd.DataFrame(rows)
    keys = ["universe", "timeframe", "sizing"]
    bench = out[out["book"] == "buy & hold"].set_index(keys)
    idx = pd.MultiIndex.from_frame(out[keys])
    out["bench_sharpe"] = bench["sharpe"].reindex(idx).to_numpy()
    out["bench_max_drawdown"] = bench["max_drawdown"].reindex(idx).to_numpy()
    out["passes"] = (
        (out["book"] != "buy & hold")
        & (out["sharpe"] > out["bench_sharpe"])
        & (out["max_drawdown"] > out["bench_max_drawdown"])
    )
    return out, curves


def survivors_table() -> pd.DataFrame:
    """The universe you would build if you only knew today's listed coins."""
    status = data.load(universe.SYMBOL_STATUS).set_index("symbol")["status"]
    trading = set(status[status == "TRADING"].index)
    close, qvol = data.load(universe.DAILY_CLOSE), data.load(universe.DAILY_QVOL)
    keep = [c for c in close.columns if c in trading]  # first segment of a live ticker only
    return universe.membership(close[keep], qvol[keep])


def casualties(
    expos: dict[tuple[str, str], tuple[pd.DataFrame, dict[str, pd.Series]]],
    table: pd.DataFrame,
    timeframe: str = "8h",
    book: str = "long_flat_gap_cd",
) -> pd.DataFrame:
    """Members that were later delisted or stopped trading: what holding them cost vs the book."""
    status = data.load(universe.SYMBOL_STATUS).set_index("symbol")["status"]
    rows = []
    for inst in table["instrument"].unique():
        symbol = inst.split("_")[0]
        key = (universe.arctic_symbol(inst), timeframe)
        if key not in expos or not expos[key][1]:
            continue
        bars, exp = expos[key]
        ended = bars.index[-1] < pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=7)
        if status.get(symbol) == "TRADING" and not ended:
            continue
        mask = member_mask(table, inst, bars.index)
        held = mask > 0
        if not held.any():
            continue
        hold_r = books.returns(bars, mask)[held]
        book_r = books.returns(bars, exp[book] * mask)[held]
        rows.append(
            {
                "instrument": inst,
                "status": "ended" if ended else status.get(symbol, "?"),
                "months_in_universe": int((table["instrument"] == inst).sum()),
                "last_bar": bars.index[-1],
                "buy_and_hold_while_member": float((1 + hold_r).prod() - 1),
                f"{book}_while_member": float((1 + book_r).prod() - 1),
            }
        )
    return pd.DataFrame(rows).sort_values("buy_and_hold_while_member")
