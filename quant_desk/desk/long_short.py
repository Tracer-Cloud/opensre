"""Was dropping the short side hindsight? Test the decision on data it was never made on.

The long/flat variants were added after looking at BTC from 2022 on (Binance). Each long/short
variant is paired with its long/flat twin; the **short side's contribution** is the book
``long/short - long/flat`` (what adding shorts changes), priced with its own turnover costs.
The decision generalises if, on data never used for it, the long/flat twin keeps a Sharpe at
least as high and the short side's contribution loses money.
"""

from __future__ import annotations

import pandas as pd

from desk import books, config, multi_asset, sweep, venues

BTC_BINANCE = multi_asset.arctic_symbol("BTCUSDT")


def datasets(coins: list[str]) -> list[tuple[str, str, str | None, str | None, bool]]:
    """(name, arctic symbol, start, end, decision_data)."""
    out = [
        ("BTC Bitstamp 2013–2017", venues.arctic_symbol("bitstamp"), None, "2017-08-17", False),
        ("BTC Binance 2017–2021", BTC_BINANCE, None, config.DECISION_DATA_START, False),
        ("BTC Binance 2022– (decision data)", BTC_BINANCE, config.DECISION_DATA_START, None, True),
    ]
    out += [
        (f"{c} (other coin)", multi_asset.arctic_symbol(c), None, None, False)
        for c in coins
        if c != "BTCUSDT"
    ]
    return out


def compare(
    expos: dict[tuple[str, str], tuple[pd.DataFrame, dict[str, pd.Series]]],
    sets: list[tuple[str, str, str | None, str | None, bool]],
) -> pd.DataFrame:
    rows = []
    for name, sym, start, end, decision in sets:
        for tf in config.ASSET_TIMEFRAMES:
            if (sym, tf) not in expos or not expos[(sym, tf)][1]:
                continue
            bars, exp = expos[(sym, tf)]
            ann = books.annualization(tf)
            mask = books.span_mask(bars.index, start, end)
            if mask.sum() < 50:
                continue
            for ls_name, lf_name in config.LONG_SHORT_PAIRS:
                for sizing in ("raw", "vol"):
                    ls = books.sized(bars, exp[ls_name], sizing, tf)
                    lf = books.sized(bars, exp[lf_name], sizing, tf)
                    r_ls = books.returns(bars, ls)[mask]
                    r_lf = books.returns(bars, lf)[mask]
                    r_short = books.returns(bars, ls - lf)[mask]
                    years = len(r_short) / ann**2
                    rows.append(
                        {
                            "dataset": name,
                            "decision_data": decision,
                            "timeframe": tf,
                            "pair": f"{ls_name} → {lf_name}",
                            "sizing": sizing,
                            "start": r_ls.index[0],
                            "end": r_ls.index[-1],
                            "sharpe_long_short": sweep.sharpe(r_ls, ann),
                            "sharpe_long_flat": sweep.sharpe(r_lf, ann),
                            "short_side_sharpe": sweep.sharpe(r_short, ann),
                            "short_side_per_year": float((1 + r_short).prod() ** (1 / years) - 1),
                        }
                    )
    table = pd.DataFrame(rows)
    table["long_flat_better"] = table["sharpe_long_flat"] >= table["sharpe_long_short"]
    table["short_side_loses"] = table["short_side_per_year"] < 0
    return table


def verdict(table: pd.DataFrame) -> pd.DataFrame:
    """Share of comparisons where long/flat wins, per group of data."""
    t = table.copy()
    t["group"] = t["dataset"].where(~t["dataset"].str.contains("other coin"), "8 other coins")
    g = t.groupby(["group", "decision_data"], sort=False)
    return pd.DataFrame(
        {
            "comparisons": g.size(),
            "long_flat_better": g["long_flat_better"].mean(),
            "short_side_loses": g["short_side_loses"].mean(),
            "median_short_side_sharpe": g["short_side_sharpe"].median(),
            "median_short_side_per_year": g["short_side_per_year"].median(),
        }
    ).reset_index()
