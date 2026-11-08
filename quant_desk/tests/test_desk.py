"""Pins the failure modes that silently ruin a backtest. Synthetic bars, no network."""

import logging

import numpy as np
import pandas as pd
import pytest

from desk import config, indicators, strategy, sweep


def _bars(n: int = 600, seed: int = 7) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    close = 50_000 * np.exp(np.cumsum(rng.normal(0, 0.004, n)))
    idx = pd.date_range("2026-01-01 00:15", periods=n, freq="15min", tz="UTC", name="bar_close")
    return pd.DataFrame(
        {"open": close, "high": close * 1.001, "low": close * 0.999, "close": close, "volume": 1.0},
        index=idx,
    )


def test_grid_is_212_combos_with_slow_at_least_one_and_a_half_fast() -> None:
    combos = sweep.grid()
    assert len(combos) == 212
    assert len(set(combos)) == 212
    assert all(s >= 1.5 * f for f, s in combos)


def test_csp_stream_emas_match_gs_quant_emas() -> None:
    """The live (csp) EMA that fires trades must equal the gs-quant EMA drawn on the chart."""
    bars = _bars()
    run = strategy.run(bars, [(6, 24)])[(6, 24)]
    assert len(run.fills) > 3
    fast = indicators.ema(bars["close"], 6).loc[run.fills.index]
    slow = indicators.ema(bars["close"], 24).loc[run.fills.index]
    np.testing.assert_allclose(run.fills["fast_ema"], fast, rtol=1e-12)
    np.testing.assert_allclose(run.fills["slow_ema"], slow, rtol=1e-12)


def test_no_lookahead_future_bars_cannot_change_past_trades_or_returns() -> None:
    bars = _bars()
    cut = bars.index[400]
    shocked = bars.copy()
    shocked.loc[shocked.index > cut, "close"] *= np.linspace(0.5, 2.0, (bars.index > cut).sum())
    a = strategy.run(bars, [(4, 16)])[(4, 16)]
    b = strategy.run(shocked, [(4, 16)])[(4, 16)]
    pd.testing.assert_series_equal(a.returns.loc[:cut], b.returns.loc[:cut])
    pd.testing.assert_frame_equal(a.fills.loc[:cut], b.fills.loc[:cut])


def test_returns_are_prior_position_times_move_minus_fee_on_flip() -> None:
    """Position decided at bar t's close earns t->t+1; fee is 2 bps per unit of change."""
    bars = _bars()
    run = strategy.run(bars, [(4, 16)])[(4, 16)]
    close = bars["close"]
    pos = run.positions.reindex(bars.index).fillna(0)
    fee = run.fills["fee"].reindex(bars.index, fill_value=0.0)
    expected = pos.shift(1, fill_value=0) * close.pct_change().fillna(0.0) - fee
    np.testing.assert_allclose(run.returns.reindex(bars.index), expected, atol=1e-15)

    flips = run.fills[run.fills["prev_position"] != 0]
    entries = run.fills[run.fills["prev_position"] == 0]
    assert np.allclose(flips["fee"], 2 * config.FEE_BPS * 1e-4)
    assert np.allclose(entries["fee"], config.FEE_BPS * 1e-4)
    assert set(run.fills["position"]) <= {-1, 1}


def test_agent_log_fires_on_the_same_bars_as_the_chart_markers(
    caplog: pytest.LogCaptureFixture,
) -> None:
    bars = _bars()
    with caplog.at_level(logging.INFO, logger="desk.agent"):
        run = strategy.run(bars, [(4, 16)], log_combos=[(4, 16)])[(4, 16)]
    logged = [r.getMessage().split(" | ")[0] for r in caplog.records if r.name == "desk.agent"]
    assert logged == [t.strftime("%Y-%m-%d %H:%M") for t in run.fills.index]
    assert set(run.fills.index) <= set(bars.index)


def test_plateau_needs_good_neighbours_and_positive_windows() -> None:
    rows = []
    for f in config.FAST_SPANS:
        for s in config.SLOW_SPANS:
            if s >= config.MIN_SLOW_OVER_FAST * f:
                sharpe = 2.0 if f <= 8 else -1.0  # broad good region at small fast spans
                if (f, s) == (20, 60):
                    sharpe = 5.0  # isolated spike surrounded by losers
                rows.append({"fast": f, "slow": s, "sharpe": sharpe, "positive_windows": 4})
    labelled = sweep.label_plateaus(pd.DataFrame(rows)).set_index(["fast", "slow"])
    assert labelled.loc[(20, 60), "label"] == "peak"
    assert labelled.loc[(6, 40), "label"] == "plateau"
    assert labelled.loc[(16, 60), "label"] == "loser"
