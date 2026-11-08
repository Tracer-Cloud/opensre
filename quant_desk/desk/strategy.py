"""EMA-crossover long/short strategy run as a csp stream over replayed 15m bars.

Timing (no lookahead): bars are stamped at their close. When the fast EMA crosses the slow EMA
on bar ``t``, the desk fills at bar ``t``'s close and pays the fee on that bar; the new position
earns the move from ``t`` to ``t+1`` onward. A cross up goes long (+1), a cross down goes short
(-1); the strategy is always in the market after its first cross (stop-and-reverse).
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


class Fill(csp.Struct):
    side: str  # "LONG" or "SHORT"
    price: float
    prev_position: int
    position: int
    fee: float  # fraction of equity
    fast_ema: float
    slow_ema: float


@csp.node
def ema_cross(
    close: ts[float], fast: ts[float], slow: ts[float], warmup: int, fee_bps: float
) -> csp.Outputs(ret=ts[float], position=ts[int], fill=ts[Fill]):
    """Per-bar net strategy return, the position held after the bar, and fills."""
    with csp.state():
        s_prev_close = 0.0
        s_position = 0
        s_prev_sign = 0
        s_bars = 0

    if csp.ticked(close) and csp.valid(fast, slow):
        s_bars += 1
        gross = 0.0
        if s_prev_close > 0.0:
            gross = s_position * (close / s_prev_close - 1.0)
        s_prev_close = close

        diff = fast - slow
        sign = 1 if diff > 0 else (-1 if diff < 0 else 0)
        fee = 0.0
        if s_bars > warmup and sign != 0 and s_prev_sign != 0 and sign != s_prev_sign:
            target = sign
            fee = fee_bps * 1e-4 * abs(target - s_position)
            csp.output(
                fill=Fill(
                    side="LONG" if target > 0 else "SHORT",
                    price=close,
                    prev_position=s_position,
                    position=target,
                    fee=fee,
                    fast_ema=fast,
                    slow_ema=slow,
                )
            )
            s_position = target
        if sign != 0:
            s_prev_sign = sign
        csp.output(ret=gross - fee, position=s_position)


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
            fill.fast_ema,
            ">" if fill.fast_ema > fill.slow_ema else "<",
            fill.slow_ema,
            fill.fee * 1e4,
        )


def combo_key(fast: int, slow: int) -> str:
    return f"ema_{fast}_{slow}"


@csp.graph
def desk_graph(
    bars: list[tuple[datetime, float]],
    combos: list[tuple[int, int]],
    log_combos: list[tuple[int, int]],
    fee_bps: float,
):
    """Replay the close stream once and fan it out to every (fast, slow) strategy.

    Each EMA span is computed once and shared by every combo that uses it.
    """
    close = csp.curve(float, bars)
    emas: dict[int, ts[float]] = {}
    for span in sorted({s for pair in combos for s in pair}):
        emas[span] = stats.ema(close, span=span, adjust=False)

    log_set = set(log_combos)
    for fast, slow in combos:
        key = combo_key(fast, slow)
        out = ema_cross(close, emas[fast], emas[slow], warmup=slow, fee_bps=fee_bps)
        csp.add_graph_output(f"{key}:ret", out.ret)
        csp.add_graph_output(f"{key}:position", out.position)
        csp.add_graph_output(f"{key}:fill", out.fill)
        if (fast, slow) in log_set:
            agent_log(out.fill, label=key)


@dataclass(frozen=True)
class ComboRun:
    fast: int
    slow: int
    returns: pd.Series  # net per-bar strategy return
    positions: pd.Series  # position held after each bar
    fills: pd.DataFrame  # one row per trade, indexed by the bar that fired it


def _series(points: list[tuple[datetime, float]], name: str) -> pd.Series:
    if not points:
        return pd.Series(dtype=float, name=name)
    times, values = zip(*points, strict=True)
    return pd.Series(values, index=pd.DatetimeIndex(times, tz="UTC"), name=name)


def _fills_frame(points: list[tuple[datetime, Fill]]) -> pd.DataFrame:
    cols = ["side", "price", "prev_position", "position", "fee", "fast_ema", "slow_ema"]
    rows = [{c: getattr(f, c) for c in cols} for _, f in points]
    index = pd.DatetimeIndex([t for t, _ in points], tz="UTC", name="bar_close")
    return pd.DataFrame(rows, index=index, columns=cols)


def run(
    bars: pd.DataFrame,
    combos: list[tuple[int, int]],
    log_combos: list[tuple[int, int]] | None = None,
    fee_bps: float = config.FEE_BPS,
) -> dict[tuple[int, int], ComboRun]:
    """Stream ``bars`` through csp for every combo and collect returns, positions and fills."""
    index = bars.index.tz_convert("UTC").tz_localize(None)
    stream = list(zip(index.to_pydatetime(), bars["close"].astype(float), strict=True))
    out = csp.run(
        desk_graph,
        stream,
        combos,
        log_combos or [],
        fee_bps,
        starttime=stream[0][0],
        endtime=stream[-1][0],
    )
    runs: dict[tuple[int, int], ComboRun] = {}
    for fast, slow in combos:
        key = combo_key(fast, slow)
        runs[(fast, slow)] = ComboRun(
            fast=fast,
            slow=slow,
            returns=_series(out.get(f"{key}:ret", []), "ret"),
            positions=_series(out.get(f"{key}:position", []), "position"),
            fills=_fills_frame(out.get(f"{key}:fill", [])),
        )
    return runs
