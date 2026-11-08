"""EMA strategies run as csp streams over replayed bars, plus the fill/return plumbing.

Timing (no lookahead): bars are stamped at their close. A decision on bar ``t`` fills at bar
``t``'s close and pays its fee on that bar; the new position earns the move from ``t`` to
``t+1`` onward. Nodes emit only fills; per-bar returns are rebuilt from them by
:func:`returns_from_fills` as ``previous position x bar move - fee``.

Two EMA nodes:

* :func:`ema_cross` -- the original stop-and-reverse crossover, always long or short after the
  first cross.
* :func:`ema_filtered` -- may stay flat. It holds the EMA direction only while the regime filter
  says "trend", goes flat when the regime ends or the EMAs cross back without enough gap, and
  waits ``cooldown`` bars after any change before entering again.
"""

# No ``from __future__ import annotations``: csp reads node/struct annotations at runtime.
import logging
from dataclasses import dataclass
from datetime import datetime

import csp
import pandas as pd
from csp import stats, ts

from desk import config

agent_logger = logging.getLogger("desk.agent")

FILL_DTYPES = {
    "side": "object",
    "price": "float64",
    "prev_position": "int64",
    "position": "int64",
    "fee": "float64",
    "ref_a": "float64",
    "ref_b": "float64",
}
FILL_COLUMNS = tuple(FILL_DTYPES)
EMA_REFS = {"ref_a": "fast_ema", "ref_b": "slow_ema"}


class Fill(csp.Struct):
    side: str  # "LONG", "SHORT" or "FLAT"
    price: float
    prev_position: int
    position: int
    fee: float  # fraction of equity
    ref_a: float  # EMA: fast EMA; mean reversion: middle band
    ref_b: float  # EMA: slow EMA; mean reversion: band that was touched


def make_fill(close: float, prev: int, target: int, fee_bps: float, a: float, b: float) -> Fill:
    return Fill(
        side="LONG" if target > 0 else ("SHORT" if target < 0 else "FLAT"),
        price=close,
        prev_position=prev,
        position=target,
        fee=fee_bps * 1e-4 * abs(target - prev),
        ref_a=a,
        ref_b=b,
    )


def _sign(x: float) -> int:
    return 1 if x > 0 else (-1 if x < 0 else 0)


@csp.node
def ema_cross(
    close: ts[float], fast: ts[float], slow: ts[float], warmup: int, fee_bps: float
) -> ts[Fill]:
    """Original stop-and-reverse: flip to the new side on every EMA cross after warm-up."""
    with csp.state():
        s_position = 0
        s_prev_sign = 0
        s_bars = 0

    if csp.ticked(close) and csp.valid(fast, slow):
        s_bars += 1
        sign = _sign(fast - slow)
        if s_bars > warmup and sign != 0 and s_prev_sign != 0 and sign != s_prev_sign:
            fill = make_fill(close, s_position, sign, fee_bps, fast, slow)
            s_position = sign
            csp.output(fill)
        if sign != 0:
            s_prev_sign = sign


@csp.node
def ema_filtered(
    close: ts[float],
    fast: ts[float],
    slow: ts[float],
    trend_ok: ts[bool],
    atr: ts[float],
    warmup: int,
    fee_bps: float,
    min_gap_atr: float,
    cooldown: int,
) -> ts[Fill]:
    """EMA direction, but only in a trend regime and with a gap/cooldown; flat otherwise."""
    with csp.state():
        s_position = 0
        s_bars = 0
        s_since_change = 1_000_000

    if csp.ticked(close) and csp.valid(fast, slow, trend_ok, atr):
        s_bars += 1
        s_since_change += 1
        if s_bars <= warmup:
            return
        sign = _sign(fast - slow)
        gap_ok = min_gap_atr <= 0.0 or abs(fast - slow) >= min_gap_atr * atr
        can_enter = trend_ok and sign != 0 and gap_ok and s_since_change >= cooldown
        target = s_position
        if s_position == 0:
            if can_enter:
                target = sign
        elif not trend_ok:
            target = 0
        elif sign == -s_position:
            target = sign if can_enter else 0
        if target != s_position:
            fill = make_fill(close, s_position, target, fee_bps, fast, slow)
            s_position = target
            s_since_change = 0
            csp.output(fill)


@csp.node
def agent_log(fill: ts[Fill], label: str):
    """The agent's trade log, stamped with the engine time of the bar that fired the trade."""
    if csp.ticked(fill):
        agent_logger.info(
            "%s | %s | %s @ %.2f | pos %+d -> %+d | ema_fast %.2f %s ema_slow %.2f | fee %.1f bps",
            csp.now().strftime("%Y-%m-%d %H:%M"),
            label,
            fill.side,
            fill.price,
            fill.prev_position,
            fill.position,
            fill.ref_a,
            ">" if fill.ref_a > fill.ref_b else "<",
            fill.ref_b,
            fill.fee * 1e4,
        )


def combo_key(a: float, b: float) -> str:
    return f"c_{a:g}_{b:g}".replace(".", "p")


@csp.graph
def ema_graph(
    closes: list[tuple[datetime, float]],
    trend_ok: list[tuple[datetime, bool]],
    atrs: list[tuple[datetime, float]],
    combos: list[tuple[int, int]],
    log_combos: list[tuple[int, int]],
    fee_bps: float,
    filtered: bool,
    min_gap_atr: float,
    cooldown: int,
):
    """Replay the stream once and fan it out to every (fast, slow) strategy.

    Each EMA span is computed once and shared by every combo that uses it.
    """
    close = csp.curve(float, closes)
    regime = csp.curve(bool, trend_ok) if filtered else None
    atr = csp.curve(float, atrs) if filtered else None
    emas: dict[int, ts[float]] = {}
    for span in sorted({s for pair in combos for s in pair}):
        emas[span] = stats.ema(close, span=span, adjust=False)

    log_set = set(log_combos)
    for fast, slow in combos:
        if filtered:
            fills = ema_filtered(
                close, emas[fast], emas[slow], regime, atr, slow, fee_bps, min_gap_atr, cooldown
            )
        else:
            fills = ema_cross(close, emas[fast], emas[slow], warmup=slow, fee_bps=fee_bps)
        csp.add_graph_output(combo_key(fast, slow), fills)
        if (fast, slow) in log_set:
            agent_log(fills, label=f"ema_{fast}_{slow}")


@dataclass(frozen=True)
class ComboRun:
    params: tuple[float, float]  # EMA: (fast, slow); mean reversion: (window, width)
    returns: pd.Series  # net per-bar strategy return
    positions: pd.Series  # position held after each bar
    fills: pd.DataFrame  # one row per position change, indexed by the bar that fired it

    @property
    def fast(self) -> int:
        return int(self.params[0])

    @property
    def slow(self) -> int:
        return int(self.params[1])


def to_stream(series: pd.Series) -> list[tuple[datetime, object]]:
    """A pandas series as csp curve points (naive UTC datetimes)."""
    index = series.index.tz_convert("UTC").tz_localize(None).to_pydatetime()
    return list(zip(index, series.tolist(), strict=True))


def fills_frame(points: list[tuple[datetime, Fill]], refs: dict[str, str]) -> pd.DataFrame:
    rows = [{c: getattr(f, c) for c in FILL_COLUMNS} for _, f in points]
    index = pd.DatetimeIndex([t for t, _ in points], tz="UTC", name="bar_close")
    frame = pd.DataFrame(rows, index=index, columns=list(FILL_COLUMNS)).astype(FILL_DTYPES)
    return frame.rename(columns=refs)


def returns_from_fills(close: pd.Series, fills: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
    """Per-bar net return and position: previous bar's position x this bar's move - fee."""
    position = fills["position"].astype(float).reindex(close.index).ffill().fillna(0.0).astype(int)
    fee = fills["fee"].reindex(close.index, fill_value=0.0)
    ret = position.shift(1, fill_value=0) * close.pct_change().fillna(0.0) - fee
    return ret.rename("ret"), position.rename("position")


def collect(
    close: pd.Series,
    out: dict[str, list[tuple[datetime, Fill]]],
    combos: list[tuple[float, float]],
    refs: dict[str, str],
) -> dict[tuple[float, float], ComboRun]:
    runs = {}
    for combo in combos:
        fills = fills_frame(out.get(combo_key(*combo), []), refs)
        ret, pos = returns_from_fills(close, fills)
        runs[combo] = ComboRun(params=combo, returns=ret, positions=pos, fills=fills)
    return runs


def run(
    bars: pd.DataFrame,
    combos: list[tuple[int, int]],
    log_combos: list[tuple[int, int]] | None = None,
    fee_bps: float = config.FEE_BPS,
    trend_ok: pd.Series | None = None,
    atr: pd.Series | None = None,
    min_gap_atr: float = 0.0,
    cooldown: int = 0,
) -> dict[tuple[float, float], ComboRun]:
    """Stream ``bars`` through csp for every EMA combo.

    Without ``trend_ok`` this is the original always-in crossover; with it, the flat-capable
    :func:`ema_filtered` strategy (``atr`` required when ``min_gap_atr > 0``).
    """
    close = bars["close"].astype(float)
    filtered = trend_ok is not None
    if filtered and atr is None:
        atr = pd.Series(0.0, index=bars.index)
    stream = to_stream(close)
    out = csp.run(
        ema_graph,
        stream,
        to_stream(trend_ok.astype(bool)) if filtered else [],
        to_stream(atr.fillna(float("nan")).astype(float)) if filtered else [],
        combos,
        log_combos or [],
        fee_bps,
        filtered,
        min_gap_atr,
        cooldown,
        starttime=stream[0][0],
        endtime=stream[-1][0],
    )
    return collect(close, out, combos, EMA_REFS)
