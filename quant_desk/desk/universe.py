"""Point-in-time, survivorship-free coin universe from every Binance USDT pair ever listed.

On the first day of each month the universe is the top ``UNIVERSE_SIZE`` instruments by mean
daily USDT volume over the previous ``UNIVERSE_VOLUME_DAYS``, among instruments that had already
traded ``UNIVERSE_MIN_HISTORY_DAYS`` and were still trading. Only data from *before* that day is
used. Delisted pairs (status ``BREAK``) are included: Binance still serves their history.

A ticker whose trading stopped for more than ``UNIVERSE_SPLIT_GAP_DAYS`` is split into separate
instruments (``LUNAUSDT``, ``LUNAUSDT_2``): the old LUNA collapsed and a *new* coin later
re-used the ticker, and the two must never be stitched into one price series.
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime

import pandas as pd
import requests

from desk import config, data

logger = logging.getLogger(__name__)

EXCHANGE_INFO_URL = "https://data-api.binance.vision/api/v3/exchangeInfo"
DAILY_CLOSE = "UNIVERSE_daily_close"
DAILY_QVOL = "UNIVERSE_daily_quote_volume"
SYMBOL_STATUS = "UNIVERSE_symbol_status"
MEMBERSHIP = "UNIVERSE_membership"


def is_risk_asset(base: str, all_bases: set[str]) -> bool:
    if base in config.NON_RISK_BASES:
        return False
    for suffix in config.LEVERAGED_SUFFIXES:  # BTCUP, ETHDOWN... but not JUP or SUPER
        if base.endswith(suffix) and base[: -len(suffix)] in all_bases:
            return False
    return True


def list_symbols() -> pd.DataFrame:
    """Every USDT spot pair Binance has listed, trading or delisted, minus non-risk assets."""
    resp = requests.get(EXCHANGE_INFO_URL, params={"permissions": "SPOT"}, timeout=60)
    resp.raise_for_status()
    rows = [s for s in resp.json()["symbols"] if s["quoteAsset"] == "USDT"]
    bases = {s["baseAsset"] for s in rows}
    keep = [
        {"symbol": s["symbol"], "base": s["baseAsset"], "status": s["status"]}
        for s in rows
        if is_risk_asset(s["baseAsset"], bases)
    ]
    return pd.DataFrame(keep).set_index("symbol")


def split_segments(bars: pd.DataFrame, max_gap: pd.Timedelta) -> list[pd.DataFrame]:
    """Cut a series wherever consecutive bars are more than ``max_gap`` apart."""
    if bars.empty:
        return []
    gaps = bars.index.to_series().diff() > max_gap
    seg_id = gaps.cumsum()
    return [seg for _, seg in bars.groupby(seg_id)]


def instrument_name(symbol: str, segment: int) -> str:
    return symbol if segment == 0 else f"{symbol}_{segment + 1}"


def _fetch_daily(symbol: str, start: datetime, end: datetime) -> dict[str, pd.Series]:
    raw = data.fetch_klines("1d", start, end, symbol)
    if raw.empty:
        return {}
    raw.index = raw.index + pd.Timedelta(days=1)  # close-stamp
    gap = pd.Timedelta(days=config.UNIVERSE_SPLIT_GAP_DAYS)
    out = {}
    for i, seg in enumerate(split_segments(raw, gap)):
        name = instrument_name(symbol, i)
        out[f"{name}|close"] = seg["close"]
        out[f"{name}|qvol"] = seg["quote_volume"]
    return out


def ingest_daily(workers: int = 4) -> pd.DataFrame:
    """Daily close and USDT volume for every instrument; returns the symbol table."""
    symbols = list_symbols()
    start = pd.Timestamp(config.UNIVERSE_START, tz="UTC") - pd.Timedelta(
        days=config.UNIVERSE_MIN_HISTORY_DAYS + config.UNIVERSE_VOLUME_DAYS + 10
    )
    end = pd.Timestamp(datetime.now(UTC)).floor("1D").to_pydatetime()
    series: dict[str, pd.Series] = {}

    def job(sym: str) -> dict[str, pd.Series]:
        try:
            return _fetch_daily(sym, start.to_pydatetime(), end)
        except Exception:
            logger.exception("daily klines failed for %s", sym)
            return {}

    with ThreadPoolExecutor(workers) as pool:
        for result in pool.map(job, symbols.index):
            series.update(result)
    close = pd.DataFrame({k.split("|")[0]: v for k, v in series.items() if k.endswith("|close")})
    qvol = pd.DataFrame({k.split("|")[0]: v for k, v in series.items() if k.endswith("|qvol")})
    data.store(close.sort_index(), DAILY_CLOSE)
    data.store(qvol.sort_index(), DAILY_QVOL)
    data.store(symbols.reset_index(), SYMBOL_STATUS)
    logger.info("universe: %d symbols -> %d instruments", len(symbols), close.shape[1])
    return symbols


def membership(close: pd.DataFrame, qvol: pd.DataFrame) -> pd.DataFrame:
    """Monthly top-N by trailing USDT volume, using only bars closed before each month starts."""
    starts = pd.date_range(
        pd.Timestamp(config.UNIVERSE_START, tz="UTC"), close.index.max(), freq="MS"
    )
    first_seen = close.apply(lambda s: s.first_valid_index())
    rows = []
    for d in starts:
        past = qvol.loc[
            (qvol.index <= d) & (qvol.index > d - pd.Timedelta(days=config.UNIVERSE_VOLUME_DAYS))
        ]
        last_close = close.loc[close.index <= d]
        alive = last_close.tail(3).notna().any()  # traded during the last 3 days
        old_enough = first_seen <= d - pd.Timedelta(days=config.UNIVERSE_MIN_HISTORY_DAYS)
        eligible = alive & old_enough.reindex(alive.index, fill_value=False)
        volume = past.mean().where(eligible).dropna().sort_values(ascending=False)
        for rank, (inst, vol) in enumerate(volume.head(config.UNIVERSE_SIZE).items(), start=1):
            rows.append({"month": d, "rank": rank, "instrument": inst, "usdt_volume_30d": vol})
    return pd.DataFrame(rows)


def load_membership() -> pd.DataFrame:
    """Membership table as stored; ArcticDB drops the timezone of datetime columns."""
    table = data.load(MEMBERSHIP)
    table["month"] = pd.to_datetime(table["month"], utc=True)
    return table


def build_membership() -> pd.DataFrame:
    table = membership(data.load(DAILY_CLOSE), data.load(DAILY_QVOL))
    data.store(table, MEMBERSHIP)
    return table


def arctic_symbol(instrument: str) -> str:
    return f"UNIV_{instrument}_4h"


def _segment_4h(instrument: str, start: pd.Timestamp) -> pd.DataFrame:
    symbol, _, seg = instrument.partition("_")
    segment = int(seg) - 1 if seg else 0
    now = pd.Timestamp(datetime.now(UTC)).floor("4h").to_pydatetime()
    raw = data.fetch_klines("4h", start.to_pydatetime(), now, symbol)
    raw.index = raw.index + pd.Timedelta(hours=4)
    raw.index.name = "bar_close"
    segments = split_segments(raw, pd.Timedelta(days=config.UNIVERSE_SPLIT_GAP_DAYS))
    if segment >= len(segments):
        return pd.DataFrame()
    bars = segments[segment]
    full = pd.date_range(bars.index[0], bars.index[-1], freq="4h", name="bar_close")
    bars = bars.reindex(full)
    bars["close"] = bars["close"].ffill()
    for col in ("open", "high", "low"):
        bars[col] = bars[col].fillna(bars["close"])
    bars[["volume", "quote_volume"]] = bars[["volume", "quote_volume"]].fillna(0.0)
    return bars


def ingest_members_4h(table: pd.DataFrame, workers: int = 4) -> list[str]:
    """4h bars for every instrument that was ever in the universe (from 200 days before)."""
    first = table.groupby("instrument")["month"].min()
    warm = pd.Timedelta(days=200)

    def job(inst: str) -> str | None:
        try:
            bars = _segment_4h(inst, max(first[inst] - warm, pd.Timestamp("2017-07-01", tz="UTC")))
        except Exception:
            logger.exception("4h klines failed for %s", inst)
            return None
        if bars.empty:
            return None
        data.store(bars, arctic_symbol(inst), instrument=inst, interval="4h", label="close")
        return inst

    with ThreadPoolExecutor(workers) as pool:
        return [i for i in pool.map(job, first.index) if i]
