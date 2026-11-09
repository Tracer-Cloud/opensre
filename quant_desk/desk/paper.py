"""24/7 paper trader for the pre-registered books in ``config.PAPER_BOOKS``.

Each run fetches recent bars, runs the *same* csp strategy and sizing as the study, and appends
the decision for every newly closed bar to an append-only ledger in ArcticDB. Past decisions
are never recomputed (that would quietly re-introduce hindsight). P&L is priced like the study:
next-open fills at ``config.COST_BPS``.

If the trader was offline, catch-up decisions are recorded with ``late=True`` and only take
effect from the bar on which they were actually made -- a paper trade cannot happen in the past.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import arcticdb as adb
import pandas as pd

from desk import config, data, ensemble, execution, filters, sweep, variants

logger = logging.getLogger(__name__)

LEDGER_COLUMNS = ("exposure", "close", "decided_at", "late")


@dataclass(frozen=True)
class PaperBook:
    symbol: str
    timeframe: str
    variant: str
    sizing: str  # "raw" or "vol"

    @property
    def key(self) -> str:
        return f"{self.symbol}_{self.timeframe}_{self.variant}_{self.sizing}"

    @property
    def minutes(self) -> int:
        return config.TIMEFRAMES[self.timeframe]


def paper_books() -> list[PaperBook]:
    return [PaperBook(*b) for b in config.PAPER_BOOKS]


def library() -> adb.library.Library:
    return data.library(config.PAPER_LIBRARY)


def fetch_recent(book: PaperBook, now: datetime) -> pd.DataFrame:
    """Closed bars for the last ``PAPER_LOOKBACK_DAYS`` (4h klines, or 15m for faster books)."""
    interval = "4h" if book.minutes % 240 == 0 else "15m"
    start = (now - timedelta(days=config.PAPER_LOOKBACK_DAYS)).strftime("%Y-%m-%d")
    bars = data.fetch_history(start, book.symbol, interval, end=now)
    native = 240 if interval == "4h" else 15
    return bars if book.minutes == native else data.resample_closed(bars, book.minutes)


def decide(bars: pd.DataFrame, book: PaperBook) -> pd.Series:
    """Target exposure decided at each bar's close -- identical to the study's book."""
    inputs = filters.regime_inputs(bars)
    runs = variants.run_variant(bars, variants.BY_NAME[book.variant], inputs)
    exposure = ensemble.ensemble_exposure(runs, bars.index)
    if book.sizing == "vol":
        exposure = exposure * ensemble.vol_weight(bars["close"], book.minutes)
    return exposure.rename("exposure")


def _read(lib: adb.library.Library, key: str) -> pd.DataFrame | None:
    return lib.read(key).data if lib.has_symbol(key) else None


def record(
    lib: adb.library.Library, book: PaperBook, bars: pd.DataFrame, now: datetime
) -> pd.DataFrame:
    """Append decisions for bars closed since the last run; returns the full ledger."""
    path = decide(bars, book)
    ledger = _read(lib, book.key)
    new = path.iloc[-1:] if ledger is None else path.loc[path.index > ledger.index.max()]
    if not new.empty:
        bar = pd.Timedelta(minutes=book.minutes)
        rows = pd.DataFrame(
            {
                "exposure": new.astype(float),
                "close": bars["close"].loc[new.index].astype(float),
                "decided_at": pd.Timestamp(now).tz_convert("UTC").tz_localize(None),  # naive UTC
                "late": (pd.Timestamp(now) - new.index) > bar,
            },
            index=new.index,
        )
        ledger = rows if ledger is None else pd.concat([ledger, rows])
        lib.write(book.key, ledger)
    stored = _read(lib, f"{book.key}_bars")
    merged = bars if stored is None else stored.combine_first(bars)
    lib.write(f"{book.key}_bars", merged)
    return ledger


def effective_exposure(ledger: pd.DataFrame, index: pd.DatetimeIndex) -> pd.Series:
    """Each decision applies from the first bar closed at or after the moment it was made."""
    decided = pd.to_datetime(ledger["decided_at"], utc=True)
    when = pd.Series(ledger.index, index=ledger.index)
    effective = when.where(~ledger["late"].astype(bool), decided)
    pos = index.searchsorted(pd.DatetimeIndex(effective), side="left")
    keep = pos < len(index)
    exposure = pd.Series(ledger["exposure"].to_numpy()[keep], index=index[pos[keep]])
    exposure = exposure[~exposure.index.duplicated(keep="last")]
    return exposure.reindex(index).ffill().fillna(0.0)


def performance(lib: adb.library.Library, book: PaperBook) -> pd.DataFrame:
    ledger = _read(lib, book.key)
    bars = _read(lib, f"{book.key}_bars")
    if ledger is None or bars is None:
        return pd.DataFrame()
    bars = bars.loc[bars.index >= ledger.index.min()]
    exposure = effective_exposure(ledger, bars.index)
    ret = execution.net_returns(bars, exposure, config.COST_BPS, config.EXECUTION)
    return pd.DataFrame(
        {
            "close": bars["close"],
            "exposure": exposure,
            "equity": (1 + ret).cumprod(),
            "hold_equity": bars["close"] / bars["close"].iloc[0],
        }
    )


def _status(book: PaperBook, ledger: pd.DataFrame, perf: pd.DataFrame) -> str:
    last = perf.iloc[-1]
    return (
        f"| {book.key} | {ledger.index.min():%Y-%m-%d %H:%M} | {len(ledger)} "
        f"({int(ledger['late'].sum())} late) | {perf.index[-1]:%Y-%m-%d %H:%M} | "
        f"{last['exposure']:.2f} | {last['equity'] - 1:+.2%} | {last['hold_equity'] - 1:+.2%} | "
        f"{sweep.max_drawdown(perf['equity'].pct_change().fillna(0.0)):.1%} |"
    )


def run_once(
    now: datetime | None = None,
    fetch: Callable[[PaperBook, datetime], pd.DataFrame] = fetch_recent,
) -> str:
    """One paper-trading step for every book; writes ``output/paper/`` and returns the status."""
    now = now or datetime.now(UTC)
    lib = library()
    out = config.OUTPUT_DIR / "paper"
    out.mkdir(parents=True, exist_ok=True)
    lines = []
    for book in paper_books():
        ledger = record(lib, book, fetch(book, now), now)
        perf = performance(lib, book)
        perf.join(ledger[["decided_at", "late"]]).to_csv(out / f"{book.key}.csv")
        lines.append(_status(book, ledger, perf))
        logger.info("%s", lines[-1])
    status = (
        f"# Paper trading status — {now:%Y-%m-%d %H:%M} UTC\n\n"
        f"Priced at {config.COST_LABEL}. Ledger is append-only (ArcticDB library "
        f"`{config.PAPER_LIBRARY}`).\n\n"
        "| book | started | decisions | last bar | exposure now | paper P&L | buy & hold | "
        "max DD |\n|---|---|---|---|---|---|---|---|\n" + "\n".join(lines) + "\n"
    )
    (out / "STATUS.md").write_text(status)
    return status


def seconds_until_next_bar(now: datetime, minutes: int, delay_s: int = 90) -> float:
    """Sleep until ``delay_s`` after the next bar boundary (the exchange needs a moment)."""
    nxt = pd.Timestamp(now).floor(f"{minutes}min") + pd.Timedelta(minutes=minutes)
    return max((nxt - pd.Timestamp(now)).total_seconds() + delay_s, 1.0)


def run_loop() -> None:
    """Run forever: one step per bar of the fastest book; network errors retry in 5 minutes."""
    minutes = min(b.minutes for b in paper_books())
    while True:
        try:
            run_once()
            wait = seconds_until_next_bar(datetime.now(UTC), minutes)
        except Exception:
            logger.exception("paper step failed; retrying in 5 minutes")
            wait = 300.0
        time.sleep(wait)
