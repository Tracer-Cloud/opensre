# Quant desk run — BTCUSDT 15m EMA crossover

Data: Binance public klines, 2880 × 15m bars (2026-10-09 19:00 →
2026-11-08 18:45 UTC, bars stamped at close), stored in ArcticDB
(`lmdb://data/arctic`, symbols `BTCUSDT_1m`, `BTCUSDT_15m`, `BTCUSDT_15m_ema_sweep`).
Buy & hold over the period: **-7.91%**.

Fees: 2 bps per unit of position change (entry 2 bps, long↔short flip 4 bps).
Sharpe is annualised with √(365 × 96) on 15m returns, net of fees.

## Indicators at the last bar (gs-quant timeseries)

| close | EMA 4 | EMA 64 | Bollinger 20/2σ | RSI 14 |
|---|---|---|---|---|
| 75,934.82 | 75,885.63 | 75,910.31 | 75,672.70 – 76,193.47 | 51.6 |

Volume profile (30 days, 40 buckets): point of control
**81,524 – 81,760**, 70% value area
78,931 – 84,824.

## Sweep: 212 combos ranked by Sharpe

fast 4–24 (step 2) × slow 12–96
(step 4), slow ≥ 1.5 × fast. W1–W4 = return inside each quarter of the 30 days.

| # | fast/slow | Sharpe | return | max DD | trades | W1 | W2 | W3 | W4 | neighbours' median Sharpe | label |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 4/64 | 0.76 | +1.65% | -11.7% | 99 | -3.24% | -2.17% | +0.15% | +7.23% | -2.30 | **peak** |
| 2 | 4/84 | 0.64 | +1.31% | -9.8% | 81 | -0.90% | -0.17% | -2.55% | +5.09% | -2.21 | **peak** |
| 3 | 4/68 | 0.48 | +0.87% | -11.4% | 97 | -3.03% | -2.00% | -0.76% | +6.96% | -2.62 | **peak** |
| 4 | 4/80 | 0.40 | +0.65% | -10.0% | 79 | -2.47% | +0.85% | -2.21% | +4.65% | -2.20 | **peak** |
| 5 | 4/76 | 0.06 | -0.30% | -11.3% | 79 | -2.47% | -0.79% | -1.76% | +4.88% | -2.60 | **peak** |
| 6 | 6/52 | -0.03 | -0.54% | -12.3% | 99 | -4.11% | -1.43% | -0.88% | +6.17% | -1.59 | **loser** |
| 7 | 4/60 | -0.06 | -0.62% | -11.8% | 115 | -4.30% | -1.55% | -0.75% | +6.28% | -0.89 | **loser** |
| 8 | 8/44 | -0.07 | -0.66% | -13.4% | 93 | -6.91% | +0.69% | -0.63% | +6.66% | -1.75 | **loser** |
| 9 | 6/28 | -0.09 | -0.73% | -11.6% | 135 | -7.74% | +0.26% | +0.44% | +6.85% | -2.06 | **loser** |
| 10 | 6/56 | -0.14 | -0.84% | -12.1% | 93 | -3.19% | -1.85% | -1.02% | +5.44% | -1.60 | **loser** |
| 11 | 4/72 | -0.43 | -1.63% | -12.0% | 93 | -2.71% | -1.80% | -2.42% | +5.51% | -3.01 | **loser** |
| 12 | 4/40 | -0.71 | -2.41% | -11.4% | 135 | -7.30% | +0.85% | -1.37% | +5.84% | -1.71 | **loser** |
| 13 | 8/24 | -0.78 | -2.60% | -11.5% | 127 | -7.59% | +1.05% | -2.36% | +6.82% | -1.59 | **loser** |
| 14 | 4/44 | -0.88 | -2.87% | -13.0% | 133 | -7.83% | -0.30% | -0.93% | +6.68% | -1.71 | **loser** |
| 15 | 4/56 | -0.89 | -2.88% | -13.1% | 125 | -4.99% | -2.03% | -1.21% | +5.61% | -0.14 | **loser** |

Full table: `sweep.csv`. Labels across all combos: 0 plateau,
5 peak, 207 loser (Sharpe ≤ 0).

**Plateau** = grid neighbours (±1 step) keep ≥ 50% of its
Sharpe *and* it is positive in ≥ 3/4 windows.
**Peak** = a good number that falls apart one step away or comes from one window.

### Plateaus
_none_

### Peaks
- `4/64` Sharpe 0.76 — neighbours median Sharpe -2.30 vs own 0.76; only 2/4 windows positive
- `4/84` Sharpe 0.64 — neighbours median Sharpe -2.21 vs own 0.64; only 1/4 windows positive
- `4/68` Sharpe 0.48 — neighbours median Sharpe -2.62 vs own 0.48; only 1/4 windows positive
- `4/80` Sharpe 0.40 — neighbours median Sharpe -2.20 vs own 0.40; only 2/4 windows positive
- `4/76` Sharpe 0.06 — neighbours median Sharpe -2.60 vs own 0.06; only 1/4 windows positive

## Shown on the chart: #1 `4/64`

Sharpe 0.76, return +1.65%, max drawdown -11.7%,
99 trades, fees paid 3.94% of equity.
Its best day (05 Nov) made +4.30%; without that one day the 30 days
return -2.54%.
Every LONG/SHORT in `trades.png` / `trades_zoom.png` sits on the bar whose close fired it, and
`agent.log` has one line per trade stamped with that same bar.

> This combo was picked on the same data it is shown on. It is a screen, not proof.

## Walk-forward (pick on window k, trade window k+1)

| train → test | picked | in-sample Sharpe | out-of-sample Sharpe | out-of-sample return |
|---|---|---|---|---|
| W1 → W2 | 4/84 | -1.20 | -0.13 | -0.17% |
| W2 → W3 | 4/16 | 4.31 | -1.62 | -1.26% |
| W3 → W4 | 6/28 | 0.79 | 8.75 | +6.85% |

Stitched out-of-sample W2–W4: **+5.32%** (Sharpe 2.63) vs buy & hold
-6.99% and the in-sample screen pick +5.06% over the
same span. 30 days gives only 3 out-of-sample steps — treat this as a smoke test, not evidence.

## Charts

- `trades.png` — price, EMAs, Bollinger, every fill, RSI, equity vs buy & hold, volume profile
- `trades_zoom.png` — last 2 days as candles with each trade labelled with its bar time
- `sweep_heatmap.png` — Sharpe over the whole grid, peaks and plateaus marked
- `windows.png` — top 5 combos' equity inside each window
- `walk_forward.png` — out-of-sample walk-forward vs the screen and buy & hold
