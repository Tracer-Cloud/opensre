"""Pins the failure modes that silently ruin a backtest. Synthetic bars, no network."""

import logging

import numpy as np
import pandas as pd
import pytest

from desk import (
    config,
    data,
    ensemble,
    execution,
    filters,
    indicators,
    mean_reversion,
    multi_asset,
    paper,
    robustness,
    strategy,
    sweep,
)
from desk import variants as V


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


def _shock_after(bars: pd.DataFrame, cut: pd.Timestamp) -> pd.DataFrame:
    shocked = bars.copy()
    scale = np.linspace(0.5, 2.0, int((bars.index > cut).sum()))
    for col in ("open", "high", "low", "close"):
        shocked.loc[shocked.index > cut, col] *= scale
    return shocked


@pytest.mark.parametrize("variant", V.VARIANTS, ids=lambda v: v.name)
def test_no_lookahead_future_bars_cannot_change_past_trades_or_returns(variant: V.Variant) -> None:
    """Filters, bands and EMAs are all causal: rewriting the future leaves the past alone."""
    bars = _bars(900)
    cut = bars.index[600]
    shocked = _shock_after(bars, cut)
    a = V.run_variant(bars, variant)
    b = V.run_variant(shocked, variant)
    traded = 0
    for key in a:
        pd.testing.assert_series_equal(a[key].returns.loc[:cut], b[key].returns.loc[:cut])
        pd.testing.assert_frame_equal(a[key].fills.loc[:cut], b[key].fills.loc[:cut])
        traded += len(a[key].fills.loc[:cut])
    assert traded > 0


def test_regime_masks_and_bands_are_causal_at_every_cut() -> None:
    """Recomputed on history truncated at bar t, every input must equal its full-history value
    at t. A filter that peeks even one bar ahead fails at some cut."""
    bars = _bars(700)
    full = filters.regime_inputs(bars)
    full_bands = filters.bollinger_inputs(bars["close"], config.MR_WINDOWS)
    regimes = {v.regime for v in V.VARIANTS}
    full_masks = {r: V.regime_mask(r, full) for r in regimes}
    for t in range(60, len(bars), 9):
        part = bars.iloc[: t + 1]
        inputs = filters.regime_inputs(part)
        pd.testing.assert_series_equal(inputs.iloc[-1], full.iloc[t], check_names=False)
        for r in regimes:
            assert V.regime_mask(r, inputs).iloc[-1] == full_masks[r].iloc[t], (r, t)
        bands = filters.bollinger_inputs(part["close"], config.MR_WINDOWS)
        for w in config.MR_WINDOWS:
            pd.testing.assert_series_equal(
                bands[w].iloc[-1], full_bands[w].iloc[t], check_names=False
            )


def test_returns_timing_on_a_hand_checked_example() -> None:
    """A fill at bar t costs its fee on t and earns from t+1, never the move into t."""
    idx = pd.date_range("2026-01-01 00:15", periods=4, freq="15min", tz="UTC")
    close = pd.Series([100.0, 110.0, 121.0, 108.9], index=idx)
    fills = pd.DataFrame({"position": [1, -1], "fee": [2e-4, 4e-4]}, index=idx[[1, 2]])
    ret, pos = strategy.returns_from_fills(close, fills)
    np.testing.assert_allclose(ret, [0.0, -2e-4, 0.10 - 4e-4, 0.10])
    assert pos.tolist() == [0, 1, -1, -1]


def test_fees_are_two_bps_per_unit_of_position_change() -> None:
    run = strategy.run(_bars(), [(4, 16)])[(4, 16)]
    flips = run.fills[run.fills["prev_position"] != 0]
    entries = run.fills[run.fills["prev_position"] == 0]
    assert np.allclose(flips["fee"], 2 * config.FEE_BPS * 1e-4)
    assert np.allclose(entries["fee"], config.FEE_BPS * 1e-4)
    assert set(run.fills["position"]) <= {-1, 1}


def test_repricing_matches_a_real_run_at_that_cost() -> None:
    bars = _bars()
    cheap = strategy.run(bars, [(4, 16)])
    real = strategy.run(bars, [(4, 16)], fee_bps=config.COST_BPS)[(4, 16)]
    repriced = execution.reprice(cheap, bars, config.COST_BPS, "close")[(4, 16)]
    np.testing.assert_allclose(repriced.returns, real.returns, atol=1e-15)
    np.testing.assert_allclose(repriced.fills["fee"], real.fills["fee"])


def test_filtered_strategy_is_flat_outside_the_trend_regime_and_respects_cooldown() -> None:
    bars = _bars(900)
    never = pd.Series(False, index=bars.index)
    assert strategy.run(bars, [(4, 16)], trend_ok=never)[(4, 16)].fills.empty

    regime = pd.Series(np.arange(len(bars)) % 200 < 100, index=bars.index)
    run = strategy.run(bars, [(4, 16)], trend_ok=regime, cooldown=6)[(4, 16)]
    pos = run.positions
    assert (pos[~regime] == 0).all()
    assert set(pos.unique()) == {-1, 0, 1}
    bar_no = pd.Series(np.arange(len(bars)), index=bars.index)
    changes = bar_no.loc[run.fills.index].to_numpy()
    entries = run.fills["position"].to_numpy() != 0
    gaps = np.diff(changes)
    assert (gaps[entries[1:]] >= 6).all()


def test_bollinger_fade_enters_beyond_the_band_and_exits_at_the_middle() -> None:
    idx = pd.date_range("2026-01-01 00:15", periods=40, freq="15min", tz="UTC")
    close = pd.Series(100.0 + 0.1 * np.sin(np.arange(40)), index=idx)
    close.iloc[25] = 97.0  # deep dip -> long
    close.iloc[26:30] = 99.0  # still below the middle band -> hold
    close.iloc[30:] = 101.0  # back above the middle band -> flat
    bars = pd.DataFrame({"close": close})
    run = mean_reversion.run(bars, [(10, 2.0)])[(10, 2.0)]
    assert run.fills["position"].tolist() == [1, 0]
    assert list(run.fills.index) == [idx[25], idx[30]]


def test_hourly_bars_only_contain_the_15m_bars_that_closed_inside_the_hour() -> None:
    idx = pd.date_range("2026-01-01 00:15", periods=8, freq="15min", tz="UTC", name="bar_close")
    px = pd.Series(np.arange(1.0, 9.0), index=idx)
    bars = pd.DataFrame(
        {"open": px, "high": px, "low": px, "close": px, "volume": 1.0, "quote_volume": 1.0}
    )
    hourly = data.resample_closed(bars, 60)
    assert list(hourly.index) == [idx[3], idx[7]]  # stamped 01:00 and 02:00
    assert hourly["open"].tolist() == [1.0, 5.0]
    assert hourly["close"].tolist() == [4.0, 8.0]
    assert hourly["volume"].tolist() == [4.0, 4.0]


def test_regime_filters_detect_trend_versus_chop() -> None:
    idx = pd.date_range("2026-01-01", periods=200, freq="1h", tz="UTC")
    trend = pd.Series(np.linspace(100, 200, 200), index=idx)
    chop = pd.Series(100 + np.where(np.arange(200) % 2, 1.0, -1.0), index=idx)
    assert filters.efficiency_ratio(trend).iloc[-1] > 0.99
    assert filters.efficiency_ratio(chop).iloc[-1] < 0.05
    ohlc = pd.DataFrame({"high": trend + 0.5, "low": trend - 0.5, "close": trend})
    assert filters.adx(ohlc).iloc[-1] > config.ADX_TREND


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


def test_long_flat_variants_never_go_short() -> None:
    bars = _bars(900)
    for name in ("long_flat", "long_flat_gap_cd", "long_flat_adx"):
        runs = V.run_variant(bars, V.BY_NAME[name])
        positions = pd.concat([r.positions for r in runs.values()])
        assert positions.min() == 0, name
        assert positions.max() == 1, name


def test_exposure_accounting_matches_fill_accounting_for_whole_positions() -> None:
    """The ensemble's fee-on-net-change maths equals the per-trade maths for one combo."""
    bars = _bars()
    run = strategy.run(bars, [(4, 16)])[(4, 16)]
    via_exposure = execution.net_returns(bars, run.positions, config.FEE_BPS, "close")
    np.testing.assert_allclose(via_exposure, run.returns, atol=1e-15)


def test_ensemble_exposure_is_the_average_position() -> None:
    bars = _bars()
    runs = strategy.run(bars, [(4, 16), (6, 24), (8, 40)])
    exposure = ensemble.ensemble_exposure(runs, bars.index)
    expected = sum(r.positions.astype(float) for r in runs.values()) / 3
    np.testing.assert_allclose(exposure, expected)
    assert exposure.between(-1, 1).all()


def test_vol_weight_is_causal_capped_and_only_changes_at_day_start() -> None:
    idx = pd.date_range("2026-01-01 01:00", periods=24 * 90, freq="1h", tz="UTC")
    rng = np.random.default_rng(3)
    vol = np.where(np.arange(len(idx)) < len(idx) // 2, 0.002, 0.03)  # calm, then wild
    close = pd.Series(100 * np.exp(np.cumsum(rng.normal(0, vol))), index=idx)
    w = ensemble.vol_weight(close, 60)
    assert w.max() <= config.VOL_MAX_WEIGHT
    assert w.iloc[-1] < 0.5  # wild regime gets scaled down
    changes = w.index[w.diff().fillna(0) != 0]
    assert all(t.hour == 0 for t in changes)
    for t in range(24 * 31, len(idx), 37):
        assert ensemble.vol_weight(close.iloc[: t + 1], 60).iloc[-1] == w.iloc[t]


def test_robust_needs_adjacent_bar_sizes_not_just_two_passes() -> None:
    from desk.history_report import robust_books

    rows = [("ensemble a", "vol", tf, tf in {"4h", "8h"}) for tf in config.TIMEFRAMES] + [
        ("ensemble b", "vol", tf, tf in {"1h", "1d"}) for tf in config.TIMEFRAMES
    ]
    stress = pd.DataFrame(rows, columns=["book", "sizing", "timeframe", "passes"])
    verdict = robust_books(stress).set_index("book")["robust"]
    assert bool(verdict["ensemble a"]) is True
    assert bool(verdict["ensemble b"]) is False


def test_next_open_execution_fills_after_the_signal_bar_and_pays_the_cost_then() -> None:
    """Decision at bar 1's close fills at bar 2's open: the old position (flat) keeps the
    close->open gap, the new one earns open->close of bar 2; one bar late shifts it again."""
    idx = pd.date_range("2026-01-01 04:00", periods=4, freq="4h", tz="UTC")
    bars = pd.DataFrame(
        {"open": [100.0, 100.0, 102.0, 121.0], "close": [100.0, 100.0, 110.0, 121.0]}, index=idx
    )
    decided = pd.Series([0.0, 1.0, 1.0, 1.0], index=idx)
    cost = 1e-4 * 10
    nxt = execution.net_returns(bars, decided, 10, "next_open")
    np.testing.assert_allclose(nxt, [0.0, 0.0, 110 / 102 - 1 - cost, 0.10])
    late = execution.net_returns(bars, decided, 10, "next_close")
    np.testing.assert_allclose(late, [0.0, 0.0, -cost, 0.10])
    ideal = execution.net_returns(bars, decided, 10, "close")
    np.testing.assert_allclose(ideal, [0.0, -cost, 0.10, 0.10])


def test_walk_forward_cannot_pick_a_combo_that_never_traded() -> None:
    idx = pd.date_range("2026-01-01", periods=400, freq="1h", tz="UTC")
    rng = np.random.default_rng(1)
    losing = pd.Series(rng.normal(-0.001, 0.01, 400), index=idx)
    pos = pd.Series(np.arange(400) % 10 < 5, index=idx).astype(int)
    idle = strategy.ComboRun((1, 2), pd.Series(0.0, index=idx), pd.Series(0, index=idx), None)
    active = strategy.ComboRun((3, 4), losing, pos, None)
    runs = {(1, 2): idle, (3, 4): active}
    assert sweep.walk_forward(runs, idx, 4, 1.0).steps["picked"].eq("1/2").all()
    wf = sweep.walk_forward(runs, idx, 4, 1.0, min_trades=3)
    assert wf.steps["picked"].eq("3/4").all()


def test_deflated_sharpe_falls_as_more_strategies_are_tried() -> None:
    r = np.random.default_rng(7).normal(0.001, 0.01, 2000)
    few = robustness.deflated_sharpe(r, 2, 1e-4)
    many = robustness.deflated_sharpe(r, 5000, 1e-4)
    assert 0 <= many < few <= 1


def test_paper_ledger_is_append_only_and_late_decisions_never_trade_in_the_past(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(config, "ARCTIC_URI", f"lmdb://{tmp_path / 'arctic'}")
    monkeypatch.setattr(config, "OUTPUT_DIR", tmp_path / "out")
    monkeypatch.setattr(config, "PAPER_BOOKS", (("TEST", "4h", "long_flat", "raw"),))
    full = _bars(700, seed=5)
    full.index = pd.date_range("2026-01-01 04:00", periods=700, freq="4h", tz="UTC")

    def upto(n: int):
        def fetch(_book, _now):
            return full.iloc[:n]

        return fetch

    t1 = full.index[599].to_pydatetime() + pd.Timedelta(minutes=2)
    paper.run_once(now=t1, fetch=upto(600))
    lib = paper.library()
    first = lib.read("TEST_4h_long_flat_raw").data
    assert len(first) == 1 and not first["late"].any()

    t2 = full.index[629].to_pydatetime() + pd.Timedelta(minutes=2)  # offline for 29 bars
    paper.run_once(now=t2, fetch=upto(630))
    ledger = lib.read("TEST_4h_long_flat_raw").data
    pd.testing.assert_frame_equal(ledger.iloc[:1], first)
    assert len(ledger) == 31  # 1 + the 30 bars closed while it was offline
    assert ledger["late"].sum() == 29  # catch-up bars; only the newest one is on time
    exposure = paper.effective_exposure(ledger, full.index[599:630])
    assert (exposure.iloc[1:-1] == first["exposure"].iloc[0]).all()


def test_portfolio_only_averages_coins_that_are_already_trading() -> None:
    a_idx = pd.date_range("2025-01-01 04:00", periods=6 * 400, freq="4h", tz="UTC")
    b_idx = a_idx[6 * 200 :]
    a = pd.DataFrame({"open": 100.0, "close": 100.0 * 1.001 ** np.arange(len(a_idx))}, index=a_idx)
    a["open"] = a["close"].shift(1).fillna(100.0)
    b = pd.DataFrame({"close": 50.0, "open": 50.0}, index=b_idx)
    zeros = dict.fromkeys(config.ASSET_BOOKS)
    study = multi_asset.AssetStudy(
        bars={("A", "4h"): a, ("B", "4h"): b},
        exposures={
            **{("A", "4h", n): pd.Series(0.0, index=a_idx) for n in zeros},
            **{("B", "4h", n): pd.Series(0.0, index=b_idx) for n in zeros},
        },
    )
    daily = multi_asset.portfolio_daily(study, "4h", "buy & hold", "raw")
    b_starts = b_idx[0].normalize() + pd.Timedelta(days=multi_asset.WARMUP_DAYS + 1)
    before = daily.loc[daily.index < b_starts]
    after = daily.loc[daily.index > b_starts + pd.Timedelta(days=2)]
    np.testing.assert_allclose(before.iloc[-1], 1.001**6 - 1, rtol=1e-9)  # A alone
    np.testing.assert_allclose(after.iloc[0], (1.001**6 - 1) / 2, rtol=1e-9)  # A and flat B


def test_vol_matched_excess_is_zero_mean_when_sharpes_are_equal() -> None:
    """Half of buy & hold has the same Sharpe: its vol-matched excess must be exactly zero."""
    bench = pd.Series(np.random.default_rng(2).normal(0.001, 0.02, 1000))
    excess = robustness.vol_matched_excess(0.5 * bench, bench)
    np.testing.assert_allclose(excess, 0.0, atol=1e-15)
