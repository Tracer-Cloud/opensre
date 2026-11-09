"""Market data: download 1m klines from Binance, resample to 15m, persist in ArcticDB."""

from __future__ import annotations

import functools
import logging
import time
from datetime import UTC, datetime, timedelta

import arcticdb as adb
import pandas as pd
import requests

from desk import config

logger = logging.getLogger(__name__)

_KLINE_COLUMNS = (
    "open_time",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "close_time",
    "quote_volume",
    "trades",
    "taker_base",
    "taker_quote",
    "ignore",
)
_NUMERIC = ("open", "high", "low", "close", "volume", "quote_volume")


@functools.cache
def _arctic(uri: str) -> adb.Arctic:
    # LMDB must be opened once per process: a second handle on the same path sees stale data.
    return adb.Arctic(uri)


@functools.cache
def _library(uri: str, name: str) -> adb.library.Library:
    # One handle per library: each handle caches its version map, so a second handle
    # opened earlier can miss writes made through another one.
    return _arctic(uri).get_library(name, create_if_missing=True)


def library(name: str = config.ARCTIC_LIBRARY) -> adb.library.Library:
    """Open (creating if needed) a library in the local LMDB-backed ArcticDB."""
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    return _library(config.ARCTIC_URI, name)


_INTERVAL_MS = {"1m": 60_000, "15m": 900_000, "1h": 3_600_000, "4h": 14_400_000}


def fetch_klines(
    interval: str, start: datetime, end: datetime, symbol: str = config.SYMBOL
) -> pd.DataFrame:
    """Download closed klines in ``[start, end)``, indexed by the bar's *close* time."""
    step = _INTERVAL_MS[interval]
    start_ms, end_ms = int(start.timestamp() * 1000), int(end.timestamp() * 1000)
    rows: list[list[object]] = []
    session = requests.Session()
    cursor = start_ms
    while cursor < end_ms:
        resp = session.get(
            config.BINANCE_KLINES_URL,
            params={
                "symbol": symbol,
                "interval": interval,
                "startTime": cursor,
                "endTime": end_ms - 1,
                "limit": config.BINANCE_MAX_LIMIT,
            },
            timeout=30,
        )
        resp.raise_for_status()
        batch = resp.json()
        if not batch:
            break
        rows.extend(batch)
        cursor = int(batch[-1][0]) + step
        time.sleep(0.05)  # stay far below the public rate limit

    df = pd.DataFrame(rows, columns=list(_KLINE_COLUMNS))
    df["open_time"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    df = df.drop_duplicates("open_time").set_index("open_time").sort_index()
    df = df[list(_NUMERIC)].astype(float)
    expected = (end_ms - start_ms) // step
    logger.info(
        "fetched %d of %d expected %s bars (%s -> %s)", len(df), expected, interval, start, end
    )
    return df


def _last_15m_boundary(now: datetime | None = None) -> datetime:
    now = (now or datetime.now(UTC)).replace(second=0, microsecond=0)
    return now - timedelta(minutes=now.minute % 15)


def fetch_1m(
    days: int = config.LOOKBACK_DAYS, end: datetime | None = None, symbol: str = config.SYMBOL
) -> pd.DataFrame:
    """``days`` of closed 1-minute klines ending at the last full 15m boundary (open-time index)."""
    end = _last_15m_boundary(end)
    return fetch_klines(config.RAW_INTERVAL, end - timedelta(days=days), end, symbol)


def fetch_history(
    start: str = config.HISTORY_START,
    symbol: str = config.SYMBOL,
    interval: str = config.HISTORY_INTERVAL,
    end: datetime | None = None,
) -> pd.DataFrame:
    """Closed ``interval`` bars from ``start`` (or the listing date) to the last full bar,
    stamped at bar close; exchange outages are forward-filled as flat bars."""
    minutes = _INTERVAL_MS[interval] // 60_000
    begin = datetime.fromisoformat(start).replace(tzinfo=UTC)
    stop = pd.Timestamp(end or datetime.now(UTC)).floor(f"{minutes}min").to_pydatetime()
    raw = fetch_klines(interval, begin, stop, symbol)
    bars = raw.copy()
    bars.index = bars.index + pd.Timedelta(minutes=minutes)
    bars.index.name = "bar_close"
    full = pd.date_range(bars.index[0], bars.index[-1], freq=f"{minutes}min", name="bar_close")
    missing = len(full) - len(bars)
    if missing:
        logger.warning("%s: %d missing %s bars forward-filled", symbol, missing, interval)
        bars = bars.reindex(full)
        bars["close"] = bars["close"].ffill()
        for col in ("open", "high", "low"):
            bars[col] = bars[col].fillna(bars["close"])
        bars[["volume", "quote_volume"]] = bars[["volume", "quote_volume"]].fillna(0.0)
    return bars


def resample_closed(bars: pd.DataFrame, minutes: int) -> pd.DataFrame:
    """Aggregate close-stamped bars into ``minutes`` bars, still stamped at close.

    With close stamps, a bar at ``t`` covers ``(t - step, t]``, hence ``closed="right"``.
    """
    if minutes == 15:
        return bars
    agg = bars.resample(f"{minutes}min", label="right", closed="right").agg(
        {
            "open": "first",
            "high": "max",
            "low": "min",
            "close": "last",
            "volume": "sum",
            "quote_volume": "sum",
        }
    )
    return agg.dropna(subset=["close"])


def ingest_history(start: str = config.HISTORY_START) -> pd.DataFrame:
    bars = fetch_history(start)
    store(
        bars, config.SYMBOL_HISTORY, source=config.BINANCE_KLINES_URL, interval="15m", label="close"
    )
    return bars


def resample_15m(raw: pd.DataFrame) -> pd.DataFrame:
    """Aggregate 1m bars to 15m OHLCV, stamped at the bar's *close* time.

    Stamping at the close means a bar's timestamp is the moment all of its data is known,
    so a signal acted on at that timestamp cannot peek into the future.
    """
    agg = raw.resample(config.BAR_RULE, label="right", closed="left").agg(
        {
            "open": "first",
            "high": "max",
            "low": "min",
            "close": "last",
            "volume": "sum",
            "quote_volume": "sum",
        }
    )
    counts = raw["close"].resample(config.BAR_RULE, label="right", closed="left").count()
    agg["minutes"] = counts
    agg.index.name = "bar_close"
    gaps = int((counts == 0).sum())
    if gaps:
        logger.warning("%d empty 15m bars (exchange gaps) forward-filled", gaps)
        agg["close"] = agg["close"].ffill()
        for col in ("open", "high", "low"):
            agg[col] = agg[col].fillna(agg["close"])
        agg[["volume", "quote_volume"]] = agg[["volume", "quote_volume"]].fillna(0.0)
    return agg


def store(df: pd.DataFrame, symbol: str, **metadata: object) -> None:
    """Write ``df`` to ArcticDB under ``symbol`` (new version, previous versions kept)."""
    lib = library()
    lib.write(symbol, df, metadata={"written_at": datetime.now(UTC).isoformat(), **metadata})
    logger.info("stored %s: %d rows in %s", symbol, len(df), config.ARCTIC_URI)


def load(symbol: str = config.SYMBOL_BARS) -> pd.DataFrame:
    """Read the latest version of ``symbol`` back from ArcticDB."""
    return library().read(symbol).data


def ingest(days: int = config.LOOKBACK_DAYS) -> pd.DataFrame:
    """Fetch, resample and store; returns the 15m bars."""
    raw = fetch_1m(days)
    bars = resample_15m(raw)
    store(raw, config.SYMBOL_RAW, source=config.BINANCE_KLINES_URL, interval="1m")
    store(bars, config.SYMBOL_BARS, source=config.SYMBOL_RAW, rule=config.BAR_RULE, label="close")
    return bars
