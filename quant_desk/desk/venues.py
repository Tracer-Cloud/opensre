"""BTC/USD from other exchanges (Bitstamp since 2013, Coinbase since 2015), close-stamped 4h bars.

Used to check that results are not a Binance artefact, and -- via Bitstamp 2013-2017 -- to test
on BTC history that no decision in this project was ever made on.
"""

from __future__ import annotations

import logging
import time
from datetime import UTC, datetime

import pandas as pd
import requests

from desk import config, data

logger = logging.getLogger(__name__)

BITSTAMP_URL = "https://www.bitstamp.net/api/v2/ohlc/{pair}/"
COINBASE_URL = "https://api.exchange.coinbase.com/products/{product}/candles"
USER_AGENT = "quant-desk-research/1.0"
OHLCV = ["open", "high", "low", "close", "volume"]


def arctic_symbol(venue: str) -> str:
    return f"VENUE_{venue}_BTCUSD_4h"


def parse_bitstamp(rows: list[dict[str, str]], step_s: int) -> pd.DataFrame:
    """Bitstamp ``timestamp`` is the bar's *open*; re-stamp at close."""
    if not rows:
        return pd.DataFrame(columns=OHLCV)
    df = pd.DataFrame(rows)
    idx = pd.to_datetime(df["timestamp"].astype("int64") + step_s, unit="s", utc=True)
    return df[OHLCV].astype(float).set_index(idx.rename("bar_close"))


def parse_coinbase(rows: list[list[float]], granularity_s: int) -> pd.DataFrame:
    """Coinbase candles are ``[time, low, high, open, close, volume]`` (note the order), newest
    first, stamped at the bar's open; re-stamp at close and sort."""
    if not rows:
        return pd.DataFrame(columns=OHLCV)
    df = pd.DataFrame(rows, columns=["time", "low", "high", "open", "close", "volume"])
    idx = pd.to_datetime(df["time"].astype("int64") + granularity_s, unit="s", utc=True)
    return df[OHLCV].astype(float).set_index(idx.rename("bar_close")).sort_index()


def fetch_bitstamp(start: str = config.VENUE_BITSTAMP_START, step_s: int = 14_400) -> pd.DataFrame:
    session = requests.Session()
    session.headers["User-Agent"] = USER_AGENT
    cursor = int(datetime.fromisoformat(start).replace(tzinfo=UTC).timestamp())
    end = int(datetime.now(UTC).timestamp()) - step_s  # closed bars only
    parts = []
    while cursor < end:
        resp = session.get(
            BITSTAMP_URL.format(pair="btcusd"),
            params={"step": step_s, "limit": 1000, "start": cursor},
            timeout=30,
        )
        resp.raise_for_status()
        rows = resp.json()["data"]["ohlc"]
        if not rows:
            break
        parts.append(parse_bitstamp(rows, step_s))
        cursor = int(rows[-1]["timestamp"]) + step_s
        time.sleep(0.2)
    return _clean(pd.concat(parts), step_s)


def fetch_coinbase(
    start: str = config.VENUE_COINBASE_START, granularity_s: int = 3_600
) -> pd.DataFrame:
    """Hourly candles (Coinbase has no 4h granularity), resampled to 4h."""
    session = requests.Session()
    session.headers["User-Agent"] = USER_AGENT
    step = pd.Timedelta(seconds=granularity_s * 300)  # 300 candles per request
    cursor = pd.Timestamp(start, tz="UTC")
    end = pd.Timestamp.now(tz="UTC").floor("1h") - pd.Timedelta(seconds=granularity_s)
    parts = []
    while cursor < end:
        stop = min(cursor + step, end)
        resp = session.get(
            COINBASE_URL.format(product="BTC-USD"),
            params={
                "granularity": granularity_s,
                "start": cursor.isoformat(),
                "end": stop.isoformat(),
            },
            timeout=30,
        )
        resp.raise_for_status()
        parts.append(parse_coinbase(resp.json(), granularity_s))
        cursor = stop
        time.sleep(0.12)  # public limit is 10 requests/s
    hourly = _clean(pd.concat(parts), granularity_s)
    return data.resample_closed(hourly.assign(quote_volume=0.0), 240).drop(columns="quote_volume")


def _clean(df: pd.DataFrame, step_s: int) -> pd.DataFrame:
    df = df[~df.index.duplicated(keep="last")].sort_index()
    full = pd.date_range(df.index[0], df.index[-1], freq=f"{step_s}s", name="bar_close")
    df = df.reindex(full)
    df["close"] = df["close"].ffill()
    for col in ("open", "high", "low"):
        df[col] = df[col].fillna(df["close"])
    df["volume"] = df["volume"].fillna(0.0)
    if step_s == 14_400:
        df["quote_volume"] = df["volume"] * df["close"]
    return df


def ingest_venues() -> list[str]:
    ok = []
    for venue, fetch in (("bitstamp", fetch_bitstamp), ("coinbase", fetch_coinbase)):
        try:
            bars = fetch()
        except Exception:
            logger.exception("could not fetch %s; skipped", venue)
            continue
        if "quote_volume" not in bars:
            bars["quote_volume"] = bars["volume"] * bars["close"]
        data.store(bars, arctic_symbol(venue), venue=venue, pair="BTC/USD", label="close")
        logger.info("%s: %d 4h bars %s -> %s", venue, len(bars), bars.index[0], bars.index[-1])
        ok.append(venue)
    return ok


def load_venue(venue: str) -> pd.DataFrame:
    return data.load(arctic_symbol(venue))
