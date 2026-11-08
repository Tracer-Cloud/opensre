# Multi-year study — does any plateau show up?

BTC/USDT, Binance 15m klines 2022-01-01 → 2026-11-08 (170,198 bars),
resampled to 1h and 4h (bars stamped at close). Every variant is swept over its whole grid on
every bar size: 21 sweeps, 3,516 parameter sets.
Fees 2 bps per unit of position change, stress-tested at
10 bps (Binance standard taker). History split into
8 equal windows.

**Plateau** = grid neighbours keep ≥ 50% of its Sharpe *and*
profitable in ≥ 6/8 windows.
**Walk-forward (WF)**: on each window pick the variant's best combo, trade it on the next
window; W2–W8 stitched = 7 out-of-sample steps.

## Variants

- `always_in` — original: EMA cross, always long or short
- `gap_cooldown` — EMA direction, enter only with gap >= 0.25 ATR, 4-bar cooldown, flat otherwise
- `flat_er` — EMA direction only while ER(20) >= 0.30, else flat
- `flat_adx` — EMA direction only while ADX(14) >= 25, else flat
- `flat_adx_gap_cd` — ADX >= 25 + gap >= 0.25 ATR + 4-bar cooldown, else flat
- `mr_always` — fade Bollinger touches, exit at middle band
- `mr_chop` — fade Bollinger touches only while ER(20) < 0.30; flat when a trend starts

Regime filters use textbook settings (ER 20 bars ≥ 0.3,
ADX 14 ≥ 25, gap 0.25 × ATR
14, cooldown 4 bars). They were **not tuned**.

## Windows

| window | dates | BTC | move |
|---|---|---|---|
| W1 | 2022-01-01 → 2022-08-10 | 46,333 → 23,988 | -48.2% |
| W2 | 2022-08-10 → 2023-03-20 | 24,002 → 27,432 | +14.3% |
| W3 | 2023-03-20 → 2023-10-27 | 27,377 → 33,699 | +23.1% |
| W4 | 2023-10-27 → 2024-06-05 | 33,707 → 70,778 | +110.0% |
| W5 | 2024-06-05 → 2025-01-13 | 70,800 → 95,176 | +34.4% |
| W6 | 2025-01-13 → 2025-08-22 | 94,710 → 116,371 | +22.9% |
| W7 | 2025-08-22 → 2026-04-01 | 116,593 → 69,209 | -40.6% |
| W8 | 2026-04-01 → 2026-11-08 | 69,103 → 75,713 | +9.6% |

## Answer

- Plateaus at 2 bps in **7/21** sweeps:
  1h flat_adx (6), 1h flat_adx_gap_cd (13), 4h always_in (33), 4h gap_cooldown (42), 4h flat_er (16), 4h flat_adx (28), 4h flat_adx_gap_cd (35).
- Still plateaus at 10 bps in **4** sweeps:
  4h always_in (15), 4h gap_cooldown (12), 4h flat_adx (2), 4h flat_adx_gap_cd (1).
- Walk-forward Sharpe above buy & hold over the same span in **0** sweeps:
  none.

## Summary

| bars | variant | combos > 0 | plateaus (2 bps) | plateaus (10 bps) | best plateau | its Sharpe 2 / 10 bps | trades/yr | time in market | WF OOS Sharpe 2 / 10 bps | WF steps > 0 | WF OOS return / max DD | B&H OOS Sharpe | B&H OOS return / max DD |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 15m | always_in | 40/212 | **0** | 0 | - | – / – | 821 | 100% | -0.24 / -1.64 | 1/7 | -61.3% / -76% | 0.81 | +215.6% / -54% |
| 15m | gap_cooldown | 47/212 | **0** | 0 | - | – / – | 1200 | 91% | -0.24 / -1.52 | 2/7 | -57.1% / -74% | 0.81 | +215.6% / -54% |
| 15m | flat_er | 0/212 | **0** | 0 | - | – / – | 4169 | 25% | -2.45 / -13.66 | 0/7 | -95.9% / -96% | 0.81 | +215.6% / -54% |
| 15m | flat_adx | 40/212 | **0** | 0 | - | – / – | 1333 | 45% | 0.17 / -3.05 | 3/7 | -1.1% / -46% | 0.81 | +215.6% / -54% |
| 15m | flat_adx_gap_cd | 102/212 | **0** | 0 | - | – / – | 1313 | 43% | 0.23 / -2.64 | 4/7 | +8.2% / -40% | 0.81 | +215.6% / -54% |
| 15m | mr_always | 0/56 | **0** | 0 | - | – / – | 1470 | 47% | -0.78 / 0.00 | 1/7 | -54.4% / -63% | 0.81 | +215.6% / -54% |
| 15m | mr_chop | 0/56 | **0** | 0 | - | – / – | 1223 | 9% | -1.28 / 0.00 | 1/7 | -33.7% / -36% | 0.81 | +215.6% / -54% |
| 1h | always_in | 55/212 | **0** | 0 | - | – / – | 196 | 100% | -0.15 / -0.27 | 3/7 | -52.4% / -73% | 0.82 | +215.4% / -54% |
| 1h | gap_cooldown | 129/212 | **0** | 0 | - | – / – | 283 | 91% | -0.13 / -0.12 | 2/7 | -46.9% / -69% | 0.82 | +215.4% / -54% |
| 1h | flat_er | 0/212 | **0** | 0 | - | – / – | 1100 | 30% | -0.18 / -3.08 | 3/7 | -34.1% / -52% | 0.82 | +215.4% / -54% |
| 1h | flat_adx | 177/212 | **6** | 0 | 22/76 | 0.69 / -0.10 | 345 | 50% | 0.15 / -0.57 | 1/7 | -4.7% / -51% | 0.82 | +215.4% / -54% |
| 1h | flat_adx_gap_cd | 211/212 | **13** | 0 | 24/72 | 0.86 / 0.16 | 339 | 48% | 0.53 / -0.27 | 4/7 | +70.2% / -32% | 0.82 | +215.4% / -54% |
| 1h | mr_always | 0/56 | **0** | 0 | - | – / – | 371 | 49% | -0.80 / -0.99 | 1/7 | -76.1% / -81% | 0.82 | +215.4% / -54% |
| 1h | mr_chop | 8/56 | **0** | 0 | - | – / – | 282 | 8% | -0.44 / -1.45 | 1/7 | -24.4% / -31% | 0.82 | +215.4% / -54% |
| 4h | always_in | 194/212 | **33** | 15 | 12/96 | 0.75 / 0.65 | 47 | 98% | 0.29 / 0.28 | 3/7 | +12.8% / -56% | 0.82 | +215.5% / -53% |
| 4h | gap_cooldown | 207/212 | **42** | 12 | 4/88 | 0.68 / 0.54 | 68 | 91% | 0.61 / 0.37 | 5/7 | +104.1% / -55% | 0.82 | +215.5% / -53% |
| 4h | flat_er | 211/212 | **16** | 0 | 22/60 | 0.58 / -0.15 | 273 | 33% | 0.24 / -0.53 | 3/7 | +12.1% / -57% | 0.82 | +215.5% / -53% |
| 4h | flat_adx | 212/212 | **28** | 2 | 10/24 | 0.45 / 0.21 | 88 | 51% | 0.35 / 0.04 | 5/7 | +29.7% / -43% | 0.82 | +215.5% / -53% |
| 4h | flat_adx_gap_cd | 212/212 | **35** | 1 | 4/96 | 0.44 / 0.24 | 89 | 49% | 0.05 / -0.26 | 4/7 | -16.7% / -40% | 0.82 | +215.5% / -53% |
| 4h | mr_always | 2/56 | **0** | 0 | - | – / – | 87 | 48% | -0.43 / -0.56 | 2/7 | -47.2% / -67% | 0.82 | +215.5% / -53% |
| 4h | mr_chop | 16/56 | **0** | 0 | - | – / – | 62 | 6% | -0.43 / -1.14 | 4/7 | -22.8% / -38% | 0.82 | +215.5% / -53% |

`trades/yr` and `time in market` are medians over the grid. Full per-combo tables are in
`sweeps/` and in ArcticDB (`BTCUSDT_<bars>_<variant>_history_sweep`).

## Plateaus

### 1h · flat_adx — 6 plateaus (top 5)

| fast/slow | Sharpe 2 bps | Sharpe 10 bps | return | max DD | windows > 0 | neighbours' median Sharpe | time in market |
|---|---|---|---|---|---|---|---|
| 22/76 | 0.69 | -0.10 | +155.0% | -43% | 6/8 | 0.59 | 50% |
| 24/72 | 0.68 | -0.11 | +149.7% | -41% | 6/8 | 0.64 | 50% |
| 20/84 | 0.63 | -0.18 | +125.9% | -40% | 6/8 | 0.59 | 50% |
| 22/80 | 0.61 | -0.19 | +117.6% | -40% | 6/8 | 0.59 | 50% |
| 24/80 | 0.60 | -0.20 | +113.8% | -36% | 6/8 | 0.61 | 50% |

### 1h · flat_adx_gap_cd — 13 plateaus (top 5)

| fast/slow | Sharpe 2 bps | Sharpe 10 bps | return | max DD | windows > 0 | neighbours' median Sharpe | time in market |
|---|---|---|---|---|---|---|---|
| 24/72 | 0.86 | 0.16 | +245.7% | -35% | 6/8 | 0.79 | 47% |
| 24/84 | 0.85 | 0.13 | +235.1% | -33% | 6/8 | 0.81 | 47% |
| 22/92 | 0.84 | 0.12 | +231.4% | -32% | 6/8 | 0.80 | 47% |
| 24/92 | 0.83 | 0.11 | +227.7% | -34% | 6/8 | 0.81 | 47% |
| 22/76 | 0.83 | 0.11 | +223.5% | -37% | 6/8 | 0.79 | 47% |

### 4h · always_in — 33 plateaus (top 5)

| fast/slow | Sharpe 2 bps | Sharpe 10 bps | return | max DD | windows > 0 | neighbours' median Sharpe | time in market |
|---|---|---|---|---|---|---|---|
| 12/96 | 0.75 | 0.65 | +230.1% | -42% | 7/8 | 0.65 | 98% |
| 10/92 | 0.71 | 0.59 | +197.9% | -47% | 6/8 | 0.64 | 98% |
| 14/88 | 0.70 | 0.61 | +195.3% | -43% | 7/8 | 0.65 | 98% |
| 16/84 | 0.70 | 0.60 | +189.6% | -42% | 6/8 | 0.56 | 98% |
| 14/84 | 0.69 | 0.59 | +187.6% | -42% | 7/8 | 0.65 | 98% |

### 4h · gap_cooldown — 42 plateaus (top 5)

| fast/slow | Sharpe 2 bps | Sharpe 10 bps | return | max DD | windows > 0 | neighbours' median Sharpe | time in market |
|---|---|---|---|---|---|---|---|
| 4/88 | 0.68 | 0.54 | +175.0% | -41% | 6/8 | 0.50 | 93% |
| 4/96 | 0.64 | 0.50 | +149.2% | -42% | 6/8 | 0.43 | 94% |
| 14/88 | 0.61 | 0.53 | +137.4% | -47% | 6/8 | 0.52 | 94% |
| 14/76 | 0.61 | 0.52 | +136.7% | -47% | 7/8 | 0.48 | 93% |
| 4/92 | 0.60 | 0.46 | +132.9% | -42% | 6/8 | 0.50 | 94% |

### 4h · flat_er — 16 plateaus (top 5)

| fast/slow | Sharpe 2 bps | Sharpe 10 bps | return | max DD | windows > 0 | neighbours' median Sharpe | time in market |
|---|---|---|---|---|---|---|---|
| 22/60 | 0.58 | -0.15 | +90.5% | -33% | 6/8 | 0.47 | 33% |
| 24/56 | 0.56 | -0.18 | +84.2% | -37% | 6/8 | 0.52 | 33% |
| 24/52 | 0.52 | -0.22 | +73.0% | -35% | 6/8 | 0.52 | 33% |
| 22/56 | 0.52 | -0.22 | +72.9% | -36% | 6/8 | 0.48 | 33% |
| 12/96 | 0.51 | -0.22 | +70.2% | -34% | 6/8 | 0.41 | 33% |

### 4h · flat_adx — 28 plateaus (top 5)

| fast/slow | Sharpe 2 bps | Sharpe 10 bps | return | max DD | windows > 0 | neighbours' median Sharpe | time in market |
|---|---|---|---|---|---|---|---|
| 10/24 | 0.45 | 0.21 | +60.4% | -44% | 6/8 | 0.39 | 52% |
| 8/24 | 0.44 | 0.19 | +60.2% | -40% | 6/8 | 0.38 | 52% |
| 10/92 | 0.44 | 0.24 | +57.2% | -41% | 6/8 | 0.35 | 51% |
| 10/88 | 0.42 | 0.22 | +53.5% | -41% | 7/8 | 0.35 | 51% |
| 10/96 | 0.41 | 0.20 | +49.3% | -41% | 6/8 | 0.35 | 51% |

### 4h · flat_adx_gap_cd — 35 plateaus (top 5)

| fast/slow | Sharpe 2 bps | Sharpe 10 bps | return | max DD | windows > 0 | neighbours' median Sharpe | time in market |
|---|---|---|---|---|---|---|---|
| 4/96 | 0.44 | 0.24 | +58.7% | -42% | 7/8 | 0.28 | 50% |
| 8/96 | 0.43 | 0.24 | +55.9% | -43% | 6/8 | 0.32 | 50% |
| 8/92 | 0.42 | 0.23 | +52.8% | -42% | 6/8 | 0.32 | 50% |
| 10/80 | 0.41 | 0.22 | +50.7% | -41% | 6/8 | 0.34 | 50% |
| 6/32 | 0.41 | 0.19 | +49.1% | -43% | 6/8 | 0.35 | 49% |

## Walk-forward detail (top 3 by OOS Sharpe)

#### 4h · gap_cooldown

| train → test | picked | in-sample Sharpe | OOS Sharpe | OOS return |
|---|---|---|---|---|
| W1 → W2 | 6/12 | 0.44 | 2.63 | +88.6% |
| W2 → W3 | 4/32 | 2.74 | -0.17 | -7.5% |
| W3 → W4 | 4/92 | 1.01 | 0.72 | +14.8% |
| W4 → W5 | 10/28 | 1.26 | 0.58 | +10.3% |
| W5 → W6 | 4/20 | 2.01 | -1.09 | -28.9% |
| W6 → W7 | 22/48 | 0.61 | 0.58 | +10.0% |
| W7 → W8 | 4/88 | 0.71 | 0.94 | +18.3% |

#### 1h · flat_adx_gap_cd

| train → test | picked | in-sample Sharpe | OOS Sharpe | OOS return |
|---|---|---|---|---|
| W1 → W2 | 10/72 | 1.03 | 2.19 | +63.7% |
| W2 → W3 | 8/12 | 2.96 | 0.05 | -1.4% |
| W3 → W4 | 24/92 | 1.39 | 0.34 | +3.4% |
| W4 → W5 | 4/28 | 0.70 | 0.46 | +6.6% |
| W5 → W6 | 24/72 | 2.02 | -0.87 | -19.1% |
| W6 → W7 | 22/96 | -0.70 | 1.69 | +37.7% |
| W7 → W8 | 24/68 | 2.05 | -0.78 | -14.1% |

#### 4h · flat_adx

| train → test | picked | in-sample Sharpe | OOS Sharpe | OOS return |
|---|---|---|---|---|
| W1 → W2 | 6/16 | 1.07 | 1.31 | +32.7% |
| W2 → W3 | 4/12 | 2.61 | 0.07 | -1.4% |
| W3 → W4 | 6/32 | 0.84 | 0.89 | +17.8% |
| W4 → W5 | 8/32 | 1.46 | 0.58 | +9.6% |
| W5 → W6 | 8/16 | 1.95 | -1.41 | -29.0% |
| W6 → W7 | 18/68 | 0.01 | 0.32 | +3.2% |
| W7 → W8 | 4/88 | 0.86 | 0.42 | +4.7% |

## Read before trusting

- Picking the best row of the summary table is itself in-sample selection across
  21 sweeps. The walk-forward numbers are the honest ones, and only per variant.
- The regime filters and thresholds are textbook values, not tuned. Tuning them would add yet
  another layer of overfitting.
- A walk-forward Sharpe of exactly 0.00 means every window picked a setting that never
  traded in the next window — at high fees "do nothing" was the best in-sample choice.
- 2 bps assumes VIP or maker fees. If a result needs 2 bps to work, it does not work for
  most accounts.
- One asset, one exchange, one mostly-bullish 5-year history. Check other assets and paper
  trade before risking money.

## Charts

- `wf_sharpe.png` — out-of-sample walk-forward Sharpe per variant and bar size, 2 vs 10 bps
- `heatmaps_trend.png` / `heatmaps_mean_reversion.png` — Sharpe over every grid, plateaus dotted
- `wf_equity.png` — out-of-sample equity per variant vs buy & hold (log scale)
