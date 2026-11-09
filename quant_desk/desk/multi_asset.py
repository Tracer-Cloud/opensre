"""The same untuned long/flat books on other coins, one by one and as an equal-weight portfolio.

Nothing is re-fitted per coin: same grids, same filters, same vol target, same pass rule. Each
coin's first ``WARMUP_DAYS`` are dropped from strategy *and* benchmark (indicators warming up).
"""

from __future__ import annotations

import logging
import multiprocessing as mp
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass

import pandas as pd

from desk import config, data, ensemble, filters, history, sweep, variants
from desk.robustness import DAILY_ANN, to_daily

logger = logging.getLogger(__name__)

WARMUP_DAYS = 120


def arctic_symbol(symbol: str) -> str:
    return f"{symbol}_{config.ASSET_INTERVAL}_history"


def ingest_assets() -> list[str]:
    """Download every coin's 4h history into ArcticDB; returns the coins that worked."""
    ok = []
    for symbol in config.ASSETS:
        try:
            bars = data.fetch_history(config.HISTORY_START, symbol, config.ASSET_INTERVAL)
        except Exception:  # an unlisted/renamed coin should not stop the study
            logger.exception("could not fetch %s; skipped", symbol)
            continue
        data.store(bars, arctic_symbol(symbol), interval=config.ASSET_INTERVAL, label="close")
        ok.append(symbol)
    return ok


def _asset_bars(symbol: str, timeframe: str) -> pd.DataFrame:
    raw = data.load(arctic_symbol(symbol))
    return data.resample_closed(raw, config.TIMEFRAMES[timeframe]) if timeframe != "4h" else raw


def _asset_job(symbol: str, timeframe: str) -> tuple[str, str, pd.DataFrame, dict[str, pd.Series]]:
    bars = _asset_bars(symbol, timeframe)
    inputs = filters.regime_inputs(bars)
    exposures = {
        name: ensemble.ensemble_exposure(
            variants.run_variant(bars, variants.BY_NAME[name], inputs), bars.index
        )
        for name in config.ASSET_BOOKS
    }
    return symbol, timeframe, bars, exposures


@dataclass(frozen=True)
class AssetStudy:
    bars: dict[tuple[str, str], pd.DataFrame]
    exposures: dict[tuple[str, str, str], pd.Series]

    @property
    def symbols(self) -> list[str]:
        return list(dict.fromkeys(s for s, _ in self.bars))


def run_assets(symbols: list[str], workers: int = config.STUDY_WORKERS) -> AssetStudy:
    jobs = [(s, tf) for s in symbols for tf in config.ASSET_TIMEFRAMES]
    with ProcessPoolExecutor(workers, mp_context=mp.get_context("spawn")) as pool:
        results = list(pool.map(_asset_job, *zip(*jobs, strict=True)))
    bars, exposures = {}, {}
    for symbol, tf, b, exp in results:
        bars[(symbol, tf)] = b
        for name, e in exp.items():
            exposures[(symbol, tf, name)] = e
    return AssetStudy(bars=bars, exposures=exposures)


def _books(study: AssetStudy, symbol: str, tf: str) -> list[tuple[str, str, pd.Series]]:
    bars = study.bars[(symbol, tf)]
    weight = ensemble.vol_weight(bars["close"], config.TIMEFRAMES[tf])
    out = [
        ("buy & hold", "raw", pd.Series(1.0, index=bars.index)),
        ("buy & hold", "vol", weight),
    ]
    for name in config.ASSET_BOOKS:
        exposure = study.exposures[(symbol, tf, name)]
        out += [
            (f"ensemble {name}", "raw", exposure),
            (f"ensemble {name}", "vol", exposure * weight),
        ]
    return out


def _passes(table: pd.DataFrame, keys: list[str]) -> pd.DataFrame:
    bench = table[table["book"] == "buy & hold"].set_index([*keys, "sizing"])
    idx = pd.MultiIndex.from_frame(table[[*keys, "sizing"]])
    table["bench_sharpe"] = bench["sharpe"].reindex(idx).to_numpy()
    table["bench_max_drawdown"] = bench["max_drawdown"].reindex(idx).to_numpy()
    table["passes"] = (
        (table["book"] != "buy & hold")
        & (table["sharpe"] > table["bench_sharpe"])
        & (table["max_drawdown"] > table["bench_max_drawdown"])
    )
    return table


def _stats(r: pd.Series, ann: float) -> dict[str, float]:
    years = len(r) / ann**2
    total = float((1 + r).prod())
    return {
        "sharpe": sweep.sharpe(r, ann),
        "cagr": total ** (1 / years) - 1 if total > 0 and years > 0 else -1.0,
        "max_drawdown": sweep.max_drawdown(r),
    }


def per_asset(study: AssetStudy) -> pd.DataFrame:
    """Every coin x bar size x book, on that coin's own W2..W8, at headline costs."""
    rows = []
    for (symbol, tf), bars in study.bars.items():
        ann = config.annualization(config.TIMEFRAMES[tf])
        span = history.oos_span(bars)
        for name, sizing, exposure in _books(study, symbol, tf):
            r = history.book_returns(bars, exposure, config.COST_BPS, config.EXECUTION).loc[span]
            rows.append(
                {
                    "symbol": symbol,
                    "timeframe": tf,
                    "book": name,
                    "sizing": sizing,
                    "start": span[0],
                    **_stats(r, ann),
                }
            )
    return _passes(pd.DataFrame(rows), ["symbol", "timeframe"])


def portfolio_daily(study: AssetStudy, tf: str, book: str, sizing: str) -> pd.Series:
    """Equal weight across the coins trading that day (each sleeve after its warm-up)."""
    sleeves = {}
    for symbol in study.symbols:
        bars = study.bars[(symbol, tf)]
        exposure = {(n, s): e for n, s, e in _books(study, symbol, tf)}[(book, sizing)]
        daily = to_daily(history.book_returns(bars, exposure, config.COST_BPS, config.EXECUTION))
        sleeves[symbol] = daily.loc[daily.index >= daily.index[0] + pd.Timedelta(days=WARMUP_DAYS)]
    frame = pd.DataFrame(sleeves)  # NaN before a coin's sleeve starts
    return frame.mean(axis=1, skipna=True).dropna().rename(f"{book} ({sizing})")


def portfolio(study: AssetStudy) -> tuple[pd.DataFrame, dict[tuple[str, str, str], pd.Series]]:
    """Portfolio metrics on its own W2..W8 (daily), with the matched buy & hold portfolio."""
    rows, curves = [], {}
    books = ["buy & hold", *(f"ensemble {n}" for n in config.ASSET_BOOKS)]
    for tf in config.ASSET_TIMEFRAMES:
        for book in books:
            for sizing in ("raw", "vol"):
                daily = portfolio_daily(study, tf, book, sizing)
                span = daily.index[
                    sweep.window_slices(len(daily), config.HISTORY_WINDOWS)[1].start :
                ]
                r = daily.loc[span]
                curves[(tf, book, sizing)] = r
                rows.append(
                    {
                        "timeframe": tf,
                        "book": book,
                        "sizing": sizing,
                        "start": span[0],
                        "coins": int(len(study.symbols)),
                        "positive_years": int(((1 + r).groupby(r.index.year).prod() > 1).sum()),
                        "years": int(r.index.year.nunique()),
                        **_stats(r, DAILY_ANN),
                    }
                )
    return _passes(pd.DataFrame(rows), ["timeframe"]), curves


def pass_counts(per: pd.DataFrame) -> pd.DataFrame:
    """How many coins each book passes on, per bar size."""
    strat = per[per["book"] != "buy & hold"]
    return (
        strat.groupby(["book", "sizing", "timeframe"], sort=False)["passes"]
        .agg(passes="sum", coins="count")
        .reset_index()
        .assign(share=lambda d: d["passes"] / d["coins"])
    )
