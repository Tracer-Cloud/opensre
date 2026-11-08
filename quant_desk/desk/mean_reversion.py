"""Bollinger-band fade (mean reversion) run as a csp stream.

Flat -> a close below ``mid - k*sd`` goes long, above ``mid + k*sd`` goes short. A long exits
to flat once the close is back at or above the middle band (short: at or below). With a chop
filter it only enters while the regime is choppy, and bails out to flat when a trend starts.
Same timing as the EMA strategies: decide and fill at the close of bar ``t``.
"""

# No ``from __future__ import annotations``: csp reads node annotations at runtime.
from datetime import datetime

import csp
import pandas as pd
from csp import ts

from desk import config, filters
from desk.strategy import ComboRun, Fill, collect, combo_key, make_fill, to_stream

MR_REFS = {"ref_a": "mid", "ref_b": "band"}


@csp.node
def bb_fade(
    close: ts[float], mid: ts[float], sd: ts[float], chop_ok: ts[bool], k: float, fee_bps: float
) -> ts[Fill]:
    with csp.state():
        s_position = 0

    if csp.ticked(close) and csp.valid(mid, sd, chop_ok):
        if mid != mid or sd != sd:  # NaN during the rolling warm-up
            return
        upper, lower = mid + k * sd, mid - k * sd
        target, band = s_position, mid
        if s_position == 0:
            if chop_ok and close < lower:
                target, band = 1, lower
            elif chop_ok and close > upper:
                target, band = -1, upper
        else:
            back_at_mid = (s_position > 0 and close >= mid) or (s_position < 0 and close <= mid)
            if back_at_mid or not chop_ok:
                target = 0
        if target != s_position:
            fill = make_fill(close, s_position, target, fee_bps, mid, band)
            s_position = target
            csp.output(fill)


@csp.graph
def fade_graph(
    closes: list[tuple[datetime, float]],
    bands: dict[int, tuple[list[tuple[datetime, float]], list[tuple[datetime, float]]]],
    chop_ok: list[tuple[datetime, bool]],
    combos: list[tuple[int, float]],
    fee_bps: float,
):
    close = csp.curve(float, closes)
    regime = csp.curve(bool, chop_ok)
    curves = {w: (csp.curve(float, m), csp.curve(float, s)) for w, (m, s) in bands.items()}
    for window, width in combos:
        mid, sd = curves[window]
        csp.add_graph_output(
            combo_key(window, width), bb_fade(close, mid, sd, regime, width, fee_bps)
        )


def grid() -> list[tuple[int, float]]:
    return [(w, k) for w in config.MR_WINDOWS for k in config.MR_WIDTHS]


def run(
    bars: pd.DataFrame,
    combos: list[tuple[int, float]],
    chop_ok: pd.Series | None = None,
    fee_bps: float = config.FEE_BPS,
) -> dict[tuple[float, float], ComboRun]:
    close = bars["close"].astype(float)
    windows = tuple(sorted({w for w, _ in combos}))
    inputs = filters.bollinger_inputs(close, windows)
    bands = {w: (to_stream(df["mid"]), to_stream(df["sd"])) for w, df in inputs.items()}
    regime = chop_ok if chop_ok is not None else pd.Series(True, index=bars.index)
    stream = to_stream(close)
    out = csp.run(
        fade_graph,
        stream,
        bands,
        to_stream(regime.astype(bool)),
        combos,
        fee_bps,
        starttime=stream[0][0],
        endtime=stream[-1][0],
    )
    return collect(close, out, combos, MR_REFS)
