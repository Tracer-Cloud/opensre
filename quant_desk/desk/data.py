"""Market data: download 1m klines from Binance, resample to 15m, persist in ArcticDB."""

from __future__ import annotations

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
_MINUTE_MS = 60_000


def library() -> adb.library.Library:
    """Open (creating if needed) the local LMDB-backed ArcticDB library."""
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    arctic = adb.Arctic(config.ARCTIC_URI)
    return arctic.get_library(config.ARCTIC_LIBRARY, create_if_missing=True)


def fetch_1m(
    days: int = config.LOOKBACK_DAYS, end: datetime | None = None, symbol: str = config.SYMBOL
) -> pd.DataFrame:
    """Download ``days`` of closed 1-minute klines ending at the last full 15m boundary."""
    end = end or datetime.now(UTC)
    end = end.replace(second=0, microsecond=0)
    end -= timedelta(minutes=end.minute % 15)
    start = end - timedelta(days=days)
    start_ms, end_ms = int(start.timestamp() * 1000), int(end.timestamp() * 1000)

    rows: list[list[object]] = []
    session = requests.Session()
    cursor = start_ms
    while cursor < end_ms:
        resp = session.get(
            config.BINANCE_KLINES_URL,
            params={
                "symbol": symbol,
                "interval": config.RAW_INTERVAL,
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
        cursor = int(batch[-1][0]) + _MINUTE_MS
        time.sleep(0.05)  # stay far below the public rate limit

    df = pd.DataFrame(rows, columns=list(_KLINE_COLUMNS))
    df["open_time"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    df = df.drop_duplicates("open_time").set_index("open_time").sort_index()
    df = df[list(_NUMERIC)].astype(float)
    expected = days * 24 * 60
    logger.info("fetched %d of %d expected 1m bars (%s -> %s)", len(df), expected, start, end)
    return df


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
