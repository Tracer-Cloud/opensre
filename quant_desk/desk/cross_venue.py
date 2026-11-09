"""Is it a Binance artefact? The same BTC books on Bitstamp and Coinbase BTC/USD.

* Common span (all three venues trading, from 2018): Sharpe, drawdown, pass rule per venue,
  and how correlated the venues' daily book returns are.
* Bitstamp 2013-01 -> 2017-08: BTC history no decision in this project ever looked at.
"""

from __future__ import annotations

import pandas as pd

from desk import books, config, multi_asset, venues
from desk.robustness import to_daily

COMMON_START = "2018-01-01"
PRE_BINANCE_END = "2017-08-17"
LONG_FLAT = ("long_flat", "long_flat_gap_cd", "long_flat_adx")


def venue_symbol(venue: str) -> str:
    return (
        multi_asset.arctic_symbol("BTCUSDT") if venue == "binance" else venues.arctic_symbol(venue)
    )


def _rows(expos, venue: str, tf: str, start, end, period: str) -> list[dict]:
    key = (venue_symbol(venue), tf)
    if key not in expos or not expos[key][1]:
        return []
    bars, exp = expos[key]
    ann = books.annualization(tf)
    mask = books.span_mask(bars.index, start, end)
    out = []
    for sizing in ("raw", "vol"):
        hold = books.returns(bars, books.sized(bars, pd.Series(1.0, index=bars.index), sizing, tf))[
            mask
        ]
        bench = books.score(hold, ann)
        out.append(
            {
                "period": period,
                "venue": venue,
                "timeframe": tf,
                "book": "buy & hold",
                "sizing": sizing,
                **bench,
            }
        )
        for name in LONG_FLAT:
            r = books.returns(bars, books.sized(bars, exp[name], sizing, tf))[mask]
            s = books.score(r, ann)
            out.append(
                {
                    "period": period,
                    "venue": venue,
                    "timeframe": tf,
                    "book": name,
                    "sizing": sizing,
                    **s,
                    "passes": s["sharpe"] > bench["sharpe"]
                    and s["max_drawdown"] > bench["max_drawdown"],
                }
            )
    return out


def compare(expos) -> pd.DataFrame:
    rows = []
    for venue in config.VENUES:
        for tf in config.ASSET_TIMEFRAMES:
            rows += _rows(expos, venue, tf, COMMON_START, None, f"common {COMMON_START[:4]}–")
    for tf in config.ASSET_TIMEFRAMES:
        rows += _rows(
            expos, "bitstamp", tf, None, PRE_BINANCE_END, "Bitstamp 2013–2017 (never seen)"
        )
    table = pd.DataFrame(rows)
    table["passes"] = table["passes"].fillna(False).astype(bool)
    return table


def correlations(expos, tf: str = "4h", book: str = "long_flat_gap_cd") -> pd.DataFrame:
    """Correlation of the venues' daily book returns on the common span."""
    cols = {}
    for venue in config.VENUES:
        key = (venue_symbol(venue), tf)
        if key not in expos or not expos[key][1]:
            continue
        bars, exp = expos[key]
        r = books.returns(bars, books.sized(bars, exp[book], "vol", tf))
        cols[venue] = to_daily(r)
    frame = pd.DataFrame(cols).loc[COMMON_START:].dropna()
    return frame.corr()
