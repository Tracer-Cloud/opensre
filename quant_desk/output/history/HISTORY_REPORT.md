# Multi-year study — does any plateau show up?

BTC/USDT, Binance 15m klines 2022-01-01 → 2026-11-08 (170,204 bars),
resampled to 1h and 4h (bars stamped at close). Every variant is swept over its whole grid on
every bar size: 50 sweeps, 9,040 parameter sets.
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
- `long_flat` — long while EMA fast > slow, flat otherwise (never short)
- `long_flat_gap_cd` — long/flat + enter only with gap >= 0.25 ATR + 4-bar cooldown
- `long_flat_adx` — long only while EMA fast > slow and ADX(14) >= 25, else flat
- `mr_always` — fade Bollinger touches, exit at middle band
- `mr_chop` — fade Bollinger touches only while ER(20) < 0.30; flat when a trend starts

Regime filters use textbook settings (ER 20 bars ≥ 0.3,
ADX 14 ≥ 25, gap 0.25 × ATR
14, cooldown 4 bars). They were **not tuned**.

## Windows

| window | dates | BTC | move |
|---|---|---|---|
| W1 | 2022-01-01 → 2022-08-10 | 46,333 → 24,002 | -48.2% |
| W2 | 2022-08-10 → 2023-03-20 | 23,959 → 27,377 | +14.3% |
| W3 | 2023-03-20 → 2023-10-27 | 27,520 → 33,776 | +22.7% |
| W4 | 2023-10-27 → 2024-06-05 | 33,781 → 70,900 | +109.9% |
| W5 | 2024-06-05 → 2025-01-13 | 70,972 → 94,327 | +32.9% |
| W6 | 2025-01-13 → 2025-08-22 | 94,091 → 116,747 | +24.1% |
| W7 | 2025-08-22 → 2026-04-01 | 116,938 → 68,692 | -41.3% |
| W8 | 2026-04-01 → 2026-11-08 | 68,528 → 74,468 | +8.7% |

## Answer

- Plateaus at 2 bps in **29/50** sweeps:
  15m long_flat_gap_cd (19), 1h flat_adx (6), 1h flat_adx_gap_cd (13), 1h long_flat_gap_cd (2), 4h always_in (33), 4h gap_cooldown (43), 4h flat_er (16), 4h flat_adx (28), 4h flat_adx_gap_cd (38), 4h long_flat (142), 4h long_flat_gap_cd (130), 4h long_flat_adx (12), 8h always_in (44), 8h gap_cooldown (7), 8h flat_er (200), 8h flat_adx (8), 8h flat_adx_gap_cd (2), 8h long_flat (93), 8h long_flat_gap_cd (91), 8h long_flat_adx (170), 8h mr_chop (7), 1d always_in (5), 1d gap_cooldown (7), 1d flat_er (30), 1d flat_adx (157), 1d flat_adx_gap_cd (93), 1d long_flat (28), 1d long_flat_gap_cd (17), 1d long_flat_adx (2).
- Still plateaus at 10 bps in **22** sweeps:
  4h always_in (15), 4h gap_cooldown (12), 4h flat_adx (2), 4h flat_adx_gap_cd (1), 4h long_flat (124), 4h long_flat_gap_cd (115), 4h long_flat_adx (1), 8h always_in (13), 8h gap_cooldown (2), 8h flat_er (132), 8h long_flat (87), 8h long_flat_gap_cd (82), 8h long_flat_adx (135), 8h mr_chop (4), 1d always_in (2), 1d gap_cooldown (3), 1d flat_er (20), 1d flat_adx (140), 1d flat_adx_gap_cd (86), 1d long_flat (27), 1d long_flat_gap_cd (17), 1d long_flat_adx (1).
- Walk-forward Sharpe above buy & hold over the same span in **4** sweeps:
  4h long_flat_gap_cd (0.94 vs 0.81), 8h flat_er (1.02 vs 0.82), 8h long_flat_gap_cd (0.83 vs 0.82), 1d long_flat_adx (1.02 vs 0.80).

## Summary

| bars | variant | combos > 0 | plateaus (2 bps) | plateaus (10 bps) | best plateau | its Sharpe 2 / 10 bps | trades/yr | time in market | WF OOS Sharpe 2 / 10 bps | WF steps > 0 | WF OOS return / max DD | B&H OOS Sharpe | B&H OOS return / max DD |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 15m | always_in | 40/212 | **0** | 0 | - | – / – | 821 | 100% | -0.23 / -1.63 | 1/7 | -60.6% / -76% | 0.80 | +210.3% / -54% |
| 15m | gap_cooldown | 49/212 | **0** | 0 | - | – / – | 1200 | 91% | -0.23 / -1.51 | 2/7 | -56.5% / -74% | 0.80 | +210.3% / -54% |
| 15m | flat_er | 0/212 | **0** | 0 | - | – / – | 4169 | 25% | -2.43 / -13.64 | 0/7 | -95.8% / -96% | 0.80 | +210.3% / -54% |
| 15m | flat_adx | 39/212 | **0** | 0 | - | – / – | 1333 | 45% | 0.17 / -3.06 | 3/7 | -1.7% / -46% | 0.80 | +210.3% / -54% |
| 15m | flat_adx_gap_cd | 99/212 | **0** | 0 | - | – / – | 1313 | 43% | 0.23 / -2.65 | 4/7 | +7.6% / -40% | 0.80 | +210.3% / -54% |
| 15m | long_flat | 119/212 | **0** | 0 | - | – / – | 821 | 51% | 0.41 / -0.60 | 5/7 | +41.0% / -48% | 0.80 | +210.3% / -54% |
| 15m | long_flat_gap_cd | 134/212 | **19** | 0 | 22/88 | 0.64 / -0.32 | 630 | 48% | 0.41 / -0.68 | 4/7 | +39.6% / -50% | 0.80 | +210.3% / -54% |
| 15m | long_flat_adx | 170/212 | **0** | 0 | - | – / – | 743 | 22% | 0.72 / -1.70 | 3/7 | +85.8% / -32% | 0.80 | +210.3% / -54% |
| 15m | mr_always | 0/56 | **0** | 0 | - | – / – | 1470 | 47% | -0.76 / 0.00 | 1/7 | -53.7% / -62% | 0.80 | +210.3% / -54% |
| 15m | mr_chop | 0/56 | **0** | 0 | - | – / – | 1223 | 9% | -1.28 / 0.00 | 1/7 | -33.7% / -36% | 0.80 | +210.3% / -54% |
| 1h | always_in | 58/212 | **0** | 0 | - | – / – | 196 | 100% | -0.14 / -0.25 | 3/7 | -51.7% / -73% | 0.81 | +210.3% / -54% |
| 1h | gap_cooldown | 132/212 | **0** | 0 | - | – / – | 283 | 91% | 0.11 / -0.11 | 3/7 | -18.1% / -69% | 0.81 | +210.3% / -54% |
| 1h | flat_er | 0/212 | **0** | 0 | - | – / – | 1100 | 30% | -0.19 / -3.08 | 3/7 | -34.4% / -52% | 0.81 | +210.3% / -54% |
| 1h | flat_adx | 177/212 | **6** | 0 | 22/76 | 0.69 / -0.10 | 345 | 50% | 0.14 / -0.58 | 1/7 | -5.5% / -51% | 0.81 | +210.3% / -54% |
| 1h | flat_adx_gap_cd | 211/212 | **13** | 0 | 24/72 | 0.86 / 0.16 | 339 | 48% | 0.53 / -0.28 | 4/7 | +68.8% / -32% | 0.81 | +210.3% / -54% |
| 1h | long_flat | 208/212 | **0** | 0 | - | – / – | 196 | 51% | 0.32 / 0.25 | 4/7 | +24.5% / -64% | 0.81 | +210.3% / -54% |
| 1h | long_flat_gap_cd | 209/212 | **2** | 0 | 4/16 | 0.31 / -0.81 | 152 | 47% | 0.26 / 0.37 | 4/7 | +15.4% / -55% | 0.81 | +210.3% / -54% |
| 1h | long_flat_adx | 170/212 | **0** | 0 | - | – / – | 196 | 23% | 0.38 / -0.20 | 3/7 | +30.6% / -38% | 0.81 | +210.3% / -54% |
| 1h | mr_always | 0/56 | **0** | 0 | - | – / – | 371 | 49% | -0.79 / -1.00 | 1/7 | -75.9% / -81% | 0.81 | +210.3% / -54% |
| 1h | mr_chop | 8/56 | **0** | 0 | - | – / – | 282 | 8% | -0.44 / -1.45 | 1/7 | -24.1% / -31% | 0.81 | +210.3% / -54% |
| 4h | always_in | 194/212 | **33** | 15 | 12/96 | 0.76 / 0.66 | 47 | 98% | 0.30 / 0.29 | 3/7 | +14.6% / -56% | 0.81 | +210.3% / -53% |
| 4h | gap_cooldown | 207/212 | **43** | 12 | 4/88 | 0.69 / 0.54 | 68 | 91% | 0.62 / 0.38 | 5/7 | +107.4% / -55% | 0.81 | +210.3% / -53% |
| 4h | flat_er | 212/212 | **16** | 0 | 22/60 | 0.59 / -0.14 | 273 | 33% | 0.25 / -0.51 | 3/7 | +14.0% / -57% | 0.81 | +210.3% / -53% |
| 4h | flat_adx | 212/212 | **28** | 2 | 10/24 | 0.45 / 0.22 | 88 | 51% | 0.36 / 0.06 | 5/7 | +31.8% / -43% | 0.81 | +210.3% / -53% |
| 4h | flat_adx_gap_cd | 212/212 | **38** | 1 | 4/96 | 0.45 / 0.25 | 89 | 49% | 0.06 / -0.24 | 4/7 | -15.3% / -40% | 0.81 | +210.3% / -53% |
| 4h | long_flat | 212/212 | **142** | 124 | 12/96 | 0.96 / 0.89 | 47 | 50% | 0.74 / 0.62 | 5/7 | +119.8% / -39% | 0.81 | +210.3% / -53% |
| 4h | long_flat_gap_cd | 212/212 | **130** | 115 | 10/88 | 0.97 / 0.89 | 36 | 48% | 0.94 / 0.61 | 5/7 | +175.5% / -40% | 0.81 | +210.3% / -53% |
| 4h | long_flat_adx | 212/212 | **12** | 1 | 8/24 | 0.49 / 0.29 | 49 | 26% | 0.81 / 0.56 | 5/7 | +101.8% / -21% | 0.81 | +210.3% / -53% |
| 4h | mr_always | 2/56 | **0** | 0 | - | – / – | 87 | 48% | -0.43 / -0.56 | 2/7 | -47.2% / -67% | 0.81 | +210.3% / -53% |
| 4h | mr_chop | 16/56 | **0** | 0 | - | – / – | 62 | 6% | -0.43 / -1.14 | 4/7 | -22.8% / -38% | 0.81 | +210.3% / -53% |
| 8h | always_in | 209/212 | **44** | 13 | 8/40 | 0.70 / 0.60 | 23 | 98% | -0.05 / -0.17 | 4/7 | -40.3% / -64% | 0.82 | +210.3% / -53% |
| 8h | gap_cooldown | 212/212 | **7** | 2 | 6/40 | 0.67 / 0.57 | 35 | 91% | 0.24 / 0.16 | 4/7 | +5.7% / -54% | 0.82 | +210.3% / -53% |
| 8h | flat_er | 212/212 | **200** | 132 | 18/88 | 1.19 / 0.83 | 131 | 32% | 1.02 / 0.60 | 5/7 | +190.1% / -23% | 0.82 | +210.3% / -53% |
| 8h | flat_adx | 210/212 | **8** | 0 | 6/48 | 0.44 / 0.33 | 41 | 48% | 0.05 / -0.12 | 2/7 | -16.0% / -60% | 0.82 | +210.3% / -53% |
| 8h | flat_adx_gap_cd | 212/212 | **2** | 0 | 4/52 | 0.45 / 0.35 | 41 | 46% | 0.39 / 0.26 | 4/7 | +36.5% / -45% | 0.82 | +210.3% / -53% |
| 8h | long_flat | 212/212 | **93** | 87 | 6/40 | 0.93 / 0.84 | 23 | 50% | 0.60 / 0.72 | 5/7 | +77.8% / -39% | 0.82 | +210.3% / -53% |
| 8h | long_flat_gap_cd | 212/212 | **91** | 82 | 6/40 | 1.00 / 0.92 | 19 | 48% | 0.83 / 0.75 | 5/7 | +138.0% / -32% | 0.82 | +210.3% / -53% |
| 8h | long_flat_adx | 212/212 | **170** | 135 | 4/92 | 0.90 / 0.81 | 24 | 27% | 0.77 / 0.65 | 5/7 | +94.5% / -39% | 0.82 | +210.3% / -53% |
| 8h | mr_always | 3/56 | **0** | 0 | - | – / – | 46 | 47% | -0.52 / -0.62 | 1/7 | -52.3% / -67% | 0.82 | +210.3% / -53% |
| 8h | mr_chop | 30/56 | **7** | 4 | 26/2.75 | 1.37 / 1.21 | 32 | 7% | -0.08 / -0.27 | 4/7 | -6.0% / -30% | 0.82 | +210.3% / -53% |
| 1d | always_in | 212/212 | **5** | 2 | 4/56 | 0.57 / 0.53 | 7 | 95% | 0.10 / 0.06 | 1/7 | -24.3% / -53% | 0.80 | +210.9% / -53% |
| 1d | gap_cooldown | 206/212 | **7** | 3 | 10/80 | 0.60 / 0.58 | 11 | 89% | -0.16 / -0.19 | 1/7 | -51.5% / -65% | 0.80 | +210.9% / -53% |
| 1d | flat_er | 212/212 | **30** | 20 | 14/72 | 0.67 / 0.56 | 41 | 32% | -0.05 / -0.17 | 2/7 | -21.2% / -46% | 0.80 | +210.9% / -53% |
| 1d | flat_adx | 212/212 | **157** | 140 | 6/92 | 0.82 / 0.78 | 14 | 48% | 0.63 / 0.58 | 6/7 | +94.9% / -28% | 0.80 | +210.9% / -53% |
| 1d | flat_adx_gap_cd | 212/212 | **93** | 86 | 6/92 | 0.75 / 0.72 | 14 | 47% | 0.44 / 0.40 | 3/7 | +47.6% / -30% | 0.80 | +210.9% / -53% |
| 1d | long_flat | 212/212 | **28** | 27 | 8/88 | 0.81 / 0.79 | 7 | 51% | 0.44 / 0.41 | 3/7 | +47.1% / -34% | 0.80 | +210.9% / -53% |
| 1d | long_flat_gap_cd | 212/212 | **17** | 17 | 4/48 | 0.82 / 0.80 | 6 | 49% | 0.48 / 0.45 | 3/7 | +54.8% / -37% | 0.80 | +210.9% / -53% |
| 1d | long_flat_adx | 212/212 | **2** | 1 | 8/24 | 0.99 / 0.96 | 8 | 26% | 1.02 / 0.98 | 5/7 | +162.4% / -31% | 0.80 | +210.9% / -53% |
| 1d | mr_always | 5/56 | **0** | 0 | - | – / – | 14 | 50% | -0.58 / -0.53 | 2/7 | -65.9% / -78% | 0.80 | +210.9% / -53% |
| 1d | mr_chop | 14/56 | **0** | 0 | - | – / – | 9 | 5% | -0.20 / -0.21 | 2/7 | -13.3% / -28% | 0.80 | +210.9% / -53% |

`trades/yr` and `time in market` are medians over the grid. Full per-combo tables are in
`sweeps/` and in ArcticDB (`BTCUSDT_<bars>_<variant>_history_sweep`).

## Robust construction — no parameter is picked anywhere

Instead of choosing one combo, each trend variant trades its **whole grid at once**: exposure is
the average position of all 212 combos, netted into one book (fees on the net change). Each book
is shown at full size and **vol-targeted** (exposure × min(1, 40% /
30-day realised vol), re-set daily, no leverage). Buy & hold gets the same
vol-targeting, so every candidate is compared against the benchmark sized the same way.

**Pass rule (fixed before looking at the results):** on W2–W8 at 10 bps,
Sharpe above *and* max drawdown shallower than the matching buy & hold.
**Robust** = passes on at least two *adjacent* bar sizes
(15m → 1h → 4h → 8h → 1d), so one lucky bar size is not enough.

**Passes at 10 bps (12):** 4h ensemble long_flat (full): Sharpe 0.83 vs 0.81, max DD -33% vs -53%, 4h ensemble long_flat (vol-target): Sharpe 1.00 vs 0.83, max DD -29% vs -52%, 4h ensemble long_flat_gap_cd (full): Sharpe 0.89 vs 0.81, max DD -30% vs -53%, 4h ensemble long_flat_gap_cd (vol-target): Sharpe 1.06 vs 0.83, max DD -26% vs -52%, 8h ensemble long_flat (full): Sharpe 0.92 vs 0.82, max DD -31% vs -53%, 8h ensemble long_flat (vol-target): Sharpe 1.06 vs 0.83, max DD -26% vs -52%, 8h ensemble long_flat_gap_cd (full): Sharpe 0.91 vs 0.82, max DD -31% vs -53%, 8h ensemble long_flat_gap_cd (vol-target): Sharpe 1.04 vs 0.83, max DD -26% vs -52%, 8h ensemble long_flat_adx (full): Sharpe 0.86 vs 0.82, max DD -26% vs -53%, 8h ensemble long_flat_adx (vol-target): Sharpe 0.92 vs 0.83, max DD -24% vs -52%, 1d ensemble long_flat_adx (full): Sharpe 1.04 vs 0.80, max DD -28% vs -53%, 1d ensemble long_flat_adx (vol-target): Sharpe 0.97 vs 0.79, max DD -27% vs -52%.
At 2 bps: 17 pass.

| book | sizing | passes on | verdict |
|---|---|---|---|
| ensemble long_flat | full | 4h, 8h | **robust** |
| ensemble long_flat | vol-target | 4h, 8h | **robust** |
| ensemble long_flat_adx | full | 8h, 1d | **robust** |
| ensemble long_flat_adx | vol-target | 8h, 1d | **robust** |
| ensemble long_flat_gap_cd | full | 4h, 8h | **robust** |
| ensemble long_flat_gap_cd | vol-target | 4h, 8h | **robust** |

Main books at 10 bps (all 8 variants in
`candidates.csv`):

| bars | book | sizing | Sharpe | vs bench | CAGR | max DD | vs bench | windows > 0 | avg exposure | turnover/yr | passes |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 15m | buy & hold | full | 0.80 | 0.80 | +30.5% | -54% | -54% | 6/7 | 100% | 0× | bench |
| 15m | buy & hold | vol-target | 0.84 | 0.84 | +28.8% | -52% | -52% | 6/7 | 86% | 3× | bench |
| 15m | ensemble always_in | full | -4.11 | 0.80 | -84.1% | -100% | -54% | 0/7 | 84% | 1933× | no |
| 15m | ensemble always_in | vol-target | -4.04 | 0.84 | -77.8% | -100% | -52% | 0/7 | 73% | 1669× | no |
| 15m | ensemble long_flat | full | -2.21 | 0.80 | -51.9% | -96% | -54% | 0/7 | 52% | 966× | no |
| 15m | ensemble long_flat | vol-target | -2.13 | 0.84 | -44.5% | -92% | -52% | 0/7 | 44% | 835× | no |
| 15m | ensemble long_flat_gap_cd | full | -1.60 | 0.80 | -40.5% | -89% | -54% | 0/7 | 47% | 731× | no |
| 15m | ensemble long_flat_gap_cd | vol-target | -1.49 | 0.84 | -33.3% | -83% | -52% | 0/7 | 41% | 632× | no |
| 15m | ensemble long_flat_adx | full | -2.12 | 0.80 | -41.2% | -90% | -54% | 1/7 | 22% | 780× | no |
| 15m | ensemble long_flat_adx | vol-target | -2.10 | 0.84 | -35.9% | -85% | -52% | 1/7 | 19% | 674× | no |
| 1h | buy & hold | full | 0.81 | 0.81 | +30.5% | -54% | -54% | 6/7 | 100% | 0× | bench |
| 1h | buy & hold | vol-target | 0.85 | 0.85 | +29.0% | -52% | -52% | 6/7 | 87% | 3× | bench |
| 1h | ensemble always_in | full | -0.93 | 0.81 | -37.9% | -90% | -54% | 2/7 | 85% | 457× | no |
| 1h | ensemble always_in | vol-target | -0.72 | 0.85 | -27.2% | -81% | -52% | 2/7 | 74% | 398× | no |
| 1h | ensemble long_flat | full | -0.03 | 0.81 | -5.3% | -60% | -54% | 3/7 | 52% | 229× | no |
| 1h | ensemble long_flat | vol-target | 0.15 | 0.85 | +0.5% | -52% | -52% | 3/7 | 45% | 199× | no |
| 1h | ensemble long_flat_gap_cd | full | 0.13 | 0.81 | -0.4% | -58% | -54% | 3/7 | 47% | 174× | no |
| 1h | ensemble long_flat_gap_cd | vol-target | 0.33 | 0.85 | +5.4% | -49% | -52% | 3/7 | 41% | 151× | no |
| 1h | ensemble long_flat_adx | full | -0.21 | 0.81 | -7.3% | -53% | -54% | 2/7 | 24% | 207× | no |
| 1h | ensemble long_flat_adx | vol-target | -0.05 | 0.85 | -3.1% | -47% | -52% | 3/7 | 21% | 181× | no |
| 4h | buy & hold | full | 0.81 | 0.81 | +30.5% | -53% | -53% | 6/7 | 100% | 0× | bench |
| 4h | buy & hold | vol-target | 0.83 | 0.83 | +28.1% | -52% | -52% | 6/7 | 88% | 3× | bench |
| 4h | ensemble always_in | full | 0.29 | 0.81 | +3.7% | -47% | -53% | 4/7 | 85% | 107× | no |
| 4h | ensemble always_in | vol-target | 0.55 | 0.83 | +14.0% | -34% | -52% | 4/7 | 75% | 95× | no |
| 4h | ensemble long_flat | full | 0.83 | 0.81 | +22.3% | -33% | -53% | 6/7 | 52% | 54× | **YES** |
| 4h | ensemble long_flat | vol-target | 1.00 | 0.83 | +25.3% | -29% | -52% | 6/7 | 46% | 48× | **YES** |
| 4h | ensemble long_flat_gap_cd | full | 0.89 | 0.81 | +23.5% | -30% | -53% | 6/7 | 49% | 41× | **YES** |
| 4h | ensemble long_flat_gap_cd | vol-target | 1.06 | 0.83 | +26.1% | -26% | -52% | 6/7 | 43% | 37× | **YES** |
| 4h | ensemble long_flat_adx | full | 0.59 | 0.81 | +11.5% | -24% | -53% | 5/7 | 26% | 51× | no |
| 4h | ensemble long_flat_adx | vol-target | 0.73 | 0.83 | +13.4% | -21% | -52% | 5/7 | 23% | 46× | no |
| 8h | buy & hold | full | 0.82 | 0.82 | +30.5% | -53% | -53% | 6/7 | 100% | 0× | bench |
| 8h | buy & hold | vol-target | 0.83 | 0.83 | +28.3% | -52% | -52% | 6/7 | 88% | 4× | bench |
| 8h | ensemble always_in | full | 0.42 | 0.82 | +9.1% | -39% | -53% | 3/7 | 85% | 53× | no |
| 8h | ensemble always_in | vol-target | 0.64 | 0.83 | +17.8% | -32% | -52% | 5/7 | 75% | 49× | no |
| 8h | ensemble long_flat | full | 0.92 | 0.82 | +25.2% | -31% | -53% | 6/7 | 53% | 26× | **YES** |
| 8h | ensemble long_flat | vol-target | 1.06 | 0.83 | +27.4% | -26% | -52% | 6/7 | 48% | 24× | **YES** |
| 8h | ensemble long_flat_gap_cd | full | 0.91 | 0.82 | +24.2% | -31% | -53% | 6/7 | 50% | 21× | **YES** |
| 8h | ensemble long_flat_gap_cd | vol-target | 1.04 | 0.83 | +25.9% | -26% | -52% | 6/7 | 45% | 20× | **YES** |
| 8h | ensemble long_flat_adx | full | 0.86 | 0.82 | +18.4% | -26% | -53% | 6/7 | 28% | 26× | **YES** |
| 8h | ensemble long_flat_adx | vol-target | 0.92 | 0.83 | +18.1% | -24% | -52% | 6/7 | 26% | 24× | **YES** |
| 1d | buy & hold | full | 0.80 | 0.80 | +30.6% | -53% | -53% | 6/7 | 100% | 0× | bench |
| 1d | buy & hold | vol-target | 0.79 | 0.79 | +26.4% | -52% | -52% | 6/7 | 87% | 5× | bench |
| 1d | ensemble always_in | full | 0.31 | 0.80 | +4.3% | -50% | -53% | 2/7 | 84% | 17× | no |
| 1d | ensemble always_in | vol-target | 0.31 | 0.79 | +5.0% | -43% | -52% | 3/7 | 72% | 18× | no |
| 1d | ensemble long_flat | full | 0.79 | 0.80 | +22.3% | -34% | -53% | 6/7 | 56% | 9× | no |
| 1d | ensemble long_flat | vol-target | 0.77 | 0.79 | +19.1% | -27% | -52% | 6/7 | 50% | 10× | no |
| 1d | ensemble long_flat_gap_cd | full | 0.74 | 0.80 | +19.9% | -35% | -53% | 6/7 | 54% | 7× | no |
| 1d | ensemble long_flat_gap_cd | vol-target | 0.71 | 0.79 | +16.8% | -29% | -52% | 6/7 | 47% | 8× | no |
| 1d | ensemble long_flat_adx | full | 1.04 | 0.80 | +25.7% | -28% | -53% | 5/7 | 30% | 9× | **YES** |
| 1d | ensemble long_flat_adx | vol-target | 0.97 | 0.79 | +20.1% | -27% | -52% | 5/7 | 26% | 10× | **YES** |

The choice of *which* variant to trade is still made after seeing this table; with
8 variants × 2 sizings × 5 bar sizes, expect
a few to pass by luck. The long/flat variants were also added *after* seeing that shorting
BTC lost money, which is hindsight. The only fully clean test left is new data: paper trade.


## Plateaus

### 15m · long_flat_gap_cd — 19 plateaus (top 5)

| fast/slow | Sharpe 2 bps | Sharpe 10 bps | return | max DD | windows > 0 | neighbours' median Sharpe | time in market |
|---|---|---|---|---|---|---|---|
| 22/88 | 0.64 | -0.32 | +114.9% | -38% | 6/8 | 0.48 | 49% |
| 20/92 | 0.57 | -0.41 | +94.1% | -38% | 6/8 | 0.47 | 49% |
| 24/84 | 0.54 | -0.41 | +82.2% | -41% | 6/8 | 0.47 | 48% |
| 22/92 | 0.54 | -0.42 | +82.3% | -43% | 6/8 | 0.47 | 49% |
| 18/88 | 0.49 | -0.57 | +69.1% | -44% | 6/8 | 0.44 | 49% |

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

### 1h · long_flat_gap_cd — 2 plateaus (top 5)

| fast/slow | Sharpe 2 bps | Sharpe 10 bps | return | max DD | windows > 0 | neighbours' median Sharpe | time in market |
|---|---|---|---|---|---|---|---|
| 4/16 | 0.31 | -0.81 | +26.9% | -43% | 6/8 | 0.22 | 44% |
| 6/24 | 0.27 | -0.54 | +18.4% | -46% | 6/8 | 0.24 | 45% |

### 4h · always_in — 33 plateaus (top 5)

| fast/slow | Sharpe 2 bps | Sharpe 10 bps | return | max DD | windows > 0 | neighbours' median Sharpe | time in market |
|---|---|---|---|---|---|---|---|
| 12/96 | 0.76 | 0.66 | +235.5% | -42% | 7/8 | 0.66 | 98% |
| 10/92 | 0.72 | 0.60 | +202.8% | -47% | 6/8 | 0.65 | 98% |
| 14/88 | 0.71 | 0.62 | +200.1% | -43% | 7/8 | 0.66 | 98% |
| 16/84 | 0.70 | 0.61 | +194.3% | -42% | 6/8 | 0.57 | 98% |
| 14/84 | 0.70 | 0.60 | +192.3% | -42% | 7/8 | 0.65 | 98% |

### 4h · gap_cooldown — 43 plateaus (top 5)

| fast/slow | Sharpe 2 bps | Sharpe 10 bps | return | max DD | windows > 0 | neighbours' median Sharpe | time in market |
|---|---|---|---|---|---|---|---|
| 4/88 | 0.69 | 0.54 | +179.5% | -41% | 6/8 | 0.51 | 93% |
| 4/96 | 0.64 | 0.50 | +153.3% | -42% | 6/8 | 0.43 | 94% |
| 14/88 | 0.62 | 0.53 | +141.2% | -47% | 6/8 | 0.53 | 94% |
| 14/76 | 0.62 | 0.52 | +140.6% | -47% | 7/8 | 0.49 | 93% |
| 4/92 | 0.61 | 0.47 | +136.7% | -42% | 6/8 | 0.51 | 94% |

### 4h · flat_er — 16 plateaus (top 5)

| fast/slow | Sharpe 2 bps | Sharpe 10 bps | return | max DD | windows > 0 | neighbours' median Sharpe | time in market |
|---|---|---|---|---|---|---|---|
| 22/60 | 0.59 | -0.14 | +93.7% | -33% | 6/8 | 0.49 | 33% |
| 24/56 | 0.57 | -0.16 | +87.2% | -37% | 6/8 | 0.53 | 33% |
| 24/52 | 0.53 | -0.21 | +75.9% | -35% | 6/8 | 0.53 | 33% |
| 22/56 | 0.53 | -0.21 | +75.7% | -36% | 6/8 | 0.50 | 33% |
| 12/96 | 0.52 | -0.21 | +73.1% | -34% | 6/8 | 0.43 | 33% |

### 4h · flat_adx — 28 plateaus (top 5)

| fast/slow | Sharpe 2 bps | Sharpe 10 bps | return | max DD | windows > 0 | neighbours' median Sharpe | time in market |
|---|---|---|---|---|---|---|---|
| 10/24 | 0.45 | 0.22 | +63.0% | -44% | 6/8 | 0.40 | 52% |
| 8/24 | 0.45 | 0.20 | +62.8% | -40% | 6/8 | 0.39 | 52% |
| 10/92 | 0.44 | 0.25 | +59.8% | -41% | 6/8 | 0.36 | 51% |
| 10/88 | 0.43 | 0.23 | +56.1% | -41% | 7/8 | 0.36 | 51% |
| 10/96 | 0.42 | 0.21 | +51.7% | -41% | 6/8 | 0.36 | 51% |

### 4h · flat_adx_gap_cd — 38 plateaus (top 5)

| fast/slow | Sharpe 2 bps | Sharpe 10 bps | return | max DD | windows > 0 | neighbours' median Sharpe | time in market |
|---|---|---|---|---|---|---|---|
| 4/96 | 0.45 | 0.25 | +61.3% | -42% | 7/8 | 0.29 | 50% |
| 8/96 | 0.44 | 0.25 | +58.5% | -43% | 6/8 | 0.33 | 50% |
| 8/92 | 0.43 | 0.24 | +55.3% | -42% | 6/8 | 0.33 | 50% |
| 10/80 | 0.42 | 0.23 | +53.2% | -41% | 6/8 | 0.34 | 50% |
| 6/32 | 0.42 | 0.20 | +51.5% | -43% | 6/8 | 0.36 | 49% |

### 4h · long_flat — 142 plateaus (top 5)

| fast/slow | Sharpe 2 bps | Sharpe 10 bps | return | max DD | windows > 0 | neighbours' median Sharpe | time in market |
|---|---|---|---|---|---|---|---|
| 12/96 | 0.96 | 0.89 | +239.1% | -43% | 6/8 | 0.89 | 50% |
| 10/92 | 0.93 | 0.84 | +225.1% | -49% | 6/8 | 0.88 | 50% |
| 14/88 | 0.93 | 0.85 | +220.7% | -42% | 6/8 | 0.89 | 50% |
| 16/84 | 0.92 | 0.85 | +218.1% | -42% | 6/8 | 0.82 | 50% |
| 14/84 | 0.92 | 0.84 | +216.3% | -44% | 6/8 | 0.89 | 50% |

### 4h · long_flat_gap_cd — 130 plateaus (top 5)

| fast/slow | Sharpe 2 bps | Sharpe 10 bps | return | max DD | windows > 0 | neighbours' median Sharpe | time in market |
|---|---|---|---|---|---|---|---|
| 10/88 | 0.97 | 0.89 | +233.5% | -40% | 6/8 | 0.81 | 49% |
| 8/96 | 0.95 | 0.87 | +230.3% | -43% | 6/8 | 0.88 | 49% |
| 12/80 | 0.93 | 0.86 | +216.6% | -41% | 6/8 | 0.83 | 49% |
| 6/96 | 0.92 | 0.82 | +215.9% | -43% | 6/8 | 0.84 | 49% |
| 16/84 | 0.89 | 0.83 | +199.2% | -42% | 6/8 | 0.78 | 49% |

### 4h · long_flat_adx — 12 plateaus (top 5)

| fast/slow | Sharpe 2 bps | Sharpe 10 bps | return | max DD | windows > 0 | neighbours' median Sharpe | time in market |
|---|---|---|---|---|---|---|---|
| 8/24 | 0.49 | 0.29 | +54.7% | -44% | 6/8 | 0.44 | 25% |
| 12/96 | 0.46 | 0.30 | +47.5% | -47% | 6/8 | 0.43 | 25% |
| 6/96 | 0.43 | 0.28 | +44.6% | -50% | 6/8 | 0.39 | 25% |
| 12/92 | 0.42 | 0.27 | +42.3% | -48% | 6/8 | 0.44 | 26% |
| 12/88 | 0.42 | 0.26 | +41.4% | -50% | 6/8 | 0.43 | 26% |

### 8h · always_in — 44 plateaus (top 5)

| fast/slow | Sharpe 2 bps | Sharpe 10 bps | return | max DD | windows > 0 | neighbours' median Sharpe | time in market |
|---|---|---|---|---|---|---|---|
| 8/40 | 0.70 | 0.60 | +188.4% | -41% | 6/8 | 0.56 | 98% |
| 6/40 | 0.69 | 0.57 | +183.8% | -45% | 6/8 | 0.58 | 98% |
| 6/44 | 0.69 | 0.57 | +182.5% | -46% | 7/8 | 0.66 | 98% |
| 4/48 | 0.69 | 0.56 | +181.6% | -44% | 6/8 | 0.67 | 98% |
| 6/52 | 0.69 | 0.59 | +181.6% | -43% | 7/8 | 0.65 | 98% |

### 8h · gap_cooldown — 7 plateaus (top 5)

| fast/slow | Sharpe 2 bps | Sharpe 10 bps | return | max DD | windows > 0 | neighbours' median Sharpe | time in market |
|---|---|---|---|---|---|---|---|
| 6/40 | 0.67 | 0.57 | +166.0% | -47% | 7/8 | 0.49 | 89% |
| 6/52 | 0.58 | 0.49 | +117.7% | -49% | 6/8 | 0.45 | 91% |
| 4/60 | 0.53 | 0.44 | +97.5% | -49% | 7/8 | 0.35 | 91% |
| 6/48 | 0.50 | 0.41 | +82.5% | -49% | 6/8 | 0.48 | 90% |
| 8/28 | 0.49 | 0.39 | +79.2% | -45% | 6/8 | 0.43 | 86% |

### 8h · flat_er — 200 plateaus (top 5)

| fast/slow | Sharpe 2 bps | Sharpe 10 bps | return | max DD | windows > 0 | neighbours' median Sharpe | time in market |
|---|---|---|---|---|---|---|---|
| 18/88 | 1.19 | 0.83 | +367.6% | -48% | 8/8 | 1.05 | 32% |
| 14/96 | 1.11 | 0.75 | +314.3% | -43% | 8/8 | 1.05 | 32% |
| 18/84 | 1.10 | 0.75 | +311.9% | -48% | 7/8 | 1.05 | 32% |
| 24/76 | 1.08 | 0.72 | +296.3% | -47% | 8/8 | 1.03 | 32% |
| 20/60 | 1.08 | 0.72 | +302.2% | -39% | 7/8 | 0.98 | 32% |

### 8h · flat_adx — 8 plateaus (top 5)

| fast/slow | Sharpe 2 bps | Sharpe 10 bps | return | max DD | windows > 0 | neighbours' median Sharpe | time in market |
|---|---|---|---|---|---|---|---|
| 6/48 | 0.44 | 0.33 | +56.8% | -37% | 6/8 | 0.44 | 48% |
| 6/40 | 0.41 | 0.30 | +49.5% | -40% | 6/8 | 0.39 | 49% |
| 6/44 | 0.40 | 0.29 | +46.4% | -37% | 6/8 | 0.42 | 48% |
| 4/36 | 0.37 | 0.25 | +40.1% | -48% | 6/8 | 0.36 | 49% |
| 4/32 | 0.35 | 0.22 | +35.5% | -51% | 6/8 | 0.35 | 49% |

### 8h · flat_adx_gap_cd — 2 plateaus (top 5)

| fast/slow | Sharpe 2 bps | Sharpe 10 bps | return | max DD | windows > 0 | neighbours' median Sharpe | time in market |
|---|---|---|---|---|---|---|---|
| 4/52 | 0.45 | 0.35 | +60.0% | -36% | 6/8 | 0.41 | 47% |
| 4/48 | 0.43 | 0.33 | +55.7% | -36% | 6/8 | 0.41 | 47% |

### 8h · long_flat — 93 plateaus (top 5)

| fast/slow | Sharpe 2 bps | Sharpe 10 bps | return | max DD | windows > 0 | neighbours' median Sharpe | time in market |
|---|---|---|---|---|---|---|---|
| 6/40 | 0.93 | 0.84 | +221.3% | -41% | 6/8 | 0.85 | 51% |
| 8/40 | 0.92 | 0.84 | +212.7% | -41% | 6/8 | 0.82 | 50% |
| 6/52 | 0.91 | 0.83 | +208.8% | -44% | 6/8 | 0.88 | 50% |
| 6/44 | 0.91 | 0.82 | +208.7% | -47% | 6/8 | 0.89 | 50% |
| 4/84 | 0.91 | 0.84 | +208.8% | -38% | 6/8 | 0.83 | 50% |

### 8h · long_flat_gap_cd — 91 plateaus (top 5)

| fast/slow | Sharpe 2 bps | Sharpe 10 bps | return | max DD | windows > 0 | neighbours' median Sharpe | time in market |
|---|---|---|---|---|---|---|---|
| 6/40 | 1.00 | 0.92 | +243.0% | -42% | 6/8 | 0.86 | 48% |
| 4/48 | 0.96 | 0.88 | +225.1% | -41% | 6/8 | 0.90 | 48% |
| 6/44 | 0.95 | 0.88 | +224.5% | -45% | 6/8 | 0.88 | 48% |
| 6/48 | 0.92 | 0.85 | +206.1% | -42% | 6/8 | 0.86 | 48% |
| 8/36 | 0.91 | 0.84 | +199.5% | -39% | 6/8 | 0.77 | 48% |

### 8h · long_flat_adx — 170 plateaus (top 5)

| fast/slow | Sharpe 2 bps | Sharpe 10 bps | return | max DD | windows > 0 | neighbours' median Sharpe | time in market |
|---|---|---|---|---|---|---|---|
| 4/92 | 0.90 | 0.81 | +139.4% | -36% | 6/8 | 0.76 | 26% |
| 4/88 | 0.89 | 0.81 | +138.9% | -36% | 6/8 | 0.80 | 26% |
| 4/84 | 0.89 | 0.80 | +136.7% | -36% | 6/8 | 0.80 | 26% |
| 4/96 | 0.85 | 0.76 | +127.5% | -36% | 6/8 | 0.73 | 26% |
| 4/80 | 0.82 | 0.74 | +119.8% | -38% | 6/8 | 0.78 | 26% |

### 8h · mr_chop — 7 plateaus (top 5)

| window/width | Sharpe 2 bps | Sharpe 10 bps | return | max DD | windows > 0 | neighbours' median Sharpe | time in market |
|---|---|---|---|---|---|---|---|
| 26/2.75 | 1.37 | 1.21 | +65.7% | -5% | 6/8 | 0.83 | 3% |
| 26/3 | 1.09 | 1.00 | +40.0% | -4% | 6/8 | 0.86 | 2% |
| 20/3 | 1.06 | 0.94 | +45.5% | -4% | 7/8 | 0.82 | 3% |
| 50/1.75 | 0.74 | 0.50 | +59.8% | -19% | 6/8 | 0.57 | 11% |
| 32/2.75 | 0.72 | 0.60 | +28.3% | -7% | 6/8 | 0.70 | 2% |

### 1d · always_in — 5 plateaus (top 5)

| fast/slow | Sharpe 2 bps | Sharpe 10 bps | return | max DD | windows > 0 | neighbours' median Sharpe | time in market |
|---|---|---|---|---|---|---|---|
| 4/56 | 0.57 | 0.53 | +117.5% | -47% | 6/8 | 0.40 | 97% |
| 4/64 | 0.54 | 0.50 | +100.3% | -46% | 6/8 | 0.47 | 96% |
| 4/68 | 0.48 | 0.44 | +75.4% | -49% | 6/8 | 0.43 | 96% |
| 4/72 | 0.43 | 0.40 | +57.1% | -54% | 6/8 | 0.29 | 96% |
| 4/20 | 0.28 | 0.20 | +7.6% | -62% | 6/8 | 0.30 | 98% |

### 1d · gap_cooldown — 7 plateaus (top 5)

| fast/slow | Sharpe 2 bps | Sharpe 10 bps | return | max DD | windows > 0 | neighbours' median Sharpe | time in market |
|---|---|---|---|---|---|---|---|
| 10/80 | 0.60 | 0.58 | +128.5% | -48% | 6/8 | 0.47 | 91% |
| 4/80 | 0.49 | 0.46 | +77.9% | -55% | 6/8 | 0.36 | 90% |
| 4/84 | 0.49 | 0.46 | +77.3% | -52% | 6/8 | 0.39 | 91% |
| 12/68 | 0.46 | 0.44 | +67.6% | -46% | 6/8 | 0.38 | 90% |
| 12/72 | 0.45 | 0.43 | +64.0% | -44% | 6/8 | 0.41 | 90% |

### 1d · flat_er — 30 plateaus (top 5)

| fast/slow | Sharpe 2 bps | Sharpe 10 bps | return | max DD | windows > 0 | neighbours' median Sharpe | time in market |
|---|---|---|---|---|---|---|---|
| 14/72 | 0.67 | 0.56 | +114.9% | -33% | 6/8 | 0.64 | 32% |
| 8/96 | 0.66 | 0.54 | +108.8% | -42% | 6/8 | 0.51 | 31% |
| 16/64 | 0.65 | 0.54 | +108.2% | -33% | 6/8 | 0.59 | 32% |
| 16/72 | 0.65 | 0.53 | +106.6% | -42% | 6/8 | 0.61 | 32% |
| 14/68 | 0.64 | 0.53 | +105.1% | -33% | 6/8 | 0.62 | 32% |

### 1d · flat_adx — 157 plateaus (top 5)

| fast/slow | Sharpe 2 bps | Sharpe 10 bps | return | max DD | windows > 0 | neighbours' median Sharpe | time in market |
|---|---|---|---|---|---|---|---|
| 6/92 | 0.82 | 0.78 | +203.2% | -34% | 8/8 | 0.77 | 48% |
| 6/96 | 0.82 | 0.78 | +203.2% | -34% | 8/8 | 0.77 | 48% |
| 6/48 | 0.79 | 0.76 | +190.3% | -34% | 8/8 | 0.71 | 48% |
| 6/52 | 0.79 | 0.76 | +190.3% | -34% | 8/8 | 0.72 | 48% |
| 4/92 | 0.79 | 0.75 | +187.4% | -34% | 8/8 | 0.75 | 48% |

### 1d · flat_adx_gap_cd — 93 plateaus (top 5)

| fast/slow | Sharpe 2 bps | Sharpe 10 bps | return | max DD | windows > 0 | neighbours' median Sharpe | time in market |
|---|---|---|---|---|---|---|---|
| 6/92 | 0.75 | 0.72 | +171.1% | -34% | 7/8 | 0.72 | 48% |
| 6/96 | 0.75 | 0.72 | +171.1% | -34% | 7/8 | 0.74 | 48% |
| 8/92 | 0.74 | 0.71 | +164.8% | -34% | 6/8 | 0.71 | 47% |
| 8/96 | 0.74 | 0.71 | +164.8% | -34% | 6/8 | 0.74 | 47% |
| 10/80 | 0.73 | 0.70 | +158.7% | -34% | 7/8 | 0.69 | 47% |

### 1d · long_flat — 28 plateaus (top 5)

| fast/slow | Sharpe 2 bps | Sharpe 10 bps | return | max DD | windows > 0 | neighbours' median Sharpe | time in market |
|---|---|---|---|---|---|---|---|
| 8/88 | 0.81 | 0.79 | +176.1% | -28% | 6/8 | 0.75 | 52% |
| 4/56 | 0.80 | 0.77 | +167.2% | -32% | 6/8 | 0.67 | 50% |
| 10/88 | 0.79 | 0.78 | +171.5% | -32% | 6/8 | 0.78 | 52% |
| 8/92 | 0.79 | 0.78 | +168.4% | -31% | 6/8 | 0.79 | 52% |
| 4/64 | 0.78 | 0.75 | +159.7% | -29% | 6/8 | 0.71 | 50% |

### 1d · long_flat_gap_cd — 17 plateaus (top 5)

| fast/slow | Sharpe 2 bps | Sharpe 10 bps | return | max DD | windows > 0 | neighbours' median Sharpe | time in market |
|---|---|---|---|---|---|---|---|
| 4/48 | 0.82 | 0.80 | +174.5% | -31% | 6/8 | 0.59 | 47% |
| 4/64 | 0.78 | 0.75 | +156.7% | -30% | 6/8 | 0.71 | 49% |
| 4/56 | 0.77 | 0.74 | +152.4% | -31% | 6/8 | 0.65 | 48% |
| 4/68 | 0.77 | 0.74 | +152.8% | -33% | 6/8 | 0.72 | 49% |
| 4/52 | 0.76 | 0.73 | +149.7% | -32% | 6/8 | 0.65 | 47% |

### 1d · long_flat_adx — 2 plateaus (top 5)

| fast/slow | Sharpe 2 bps | Sharpe 10 bps | return | max DD | windows > 0 | neighbours' median Sharpe | time in market |
|---|---|---|---|---|---|---|---|
| 8/24 | 0.99 | 0.96 | +176.6% | -22% | 6/8 | 0.93 | 26% |
| 8/20 | 0.94 | 0.90 | +157.9% | -24% | 6/8 | 0.87 | 26% |

## Walk-forward detail (top 3 by OOS Sharpe)

#### 1d · long_flat_adx

| train → test | picked | in-sample Sharpe | OOS Sharpe | OOS return |
|---|---|---|---|---|
| W1 → W2 | 4/48 | 0.00 | 1.51 | +25.9% |
| W2 → W3 | 6/12 | 1.62 | 0.77 | +10.2% |
| W3 → W4 | 22/64 | 1.46 | 2.45 | +73.1% |
| W4 → W5 | 24/52 | 2.51 | 1.73 | +29.2% |
| W5 → W6 | 4/12 | 2.21 | 0.66 | +5.6% |
| W6 → W7 | 16/48 | 1.47 | -2.38 | -19.1% |
| W7 → W8 | 10/20 | -0.90 | 0.02 | -0.9% |

#### 8h · flat_er

| train → test | picked | in-sample Sharpe | OOS Sharpe | OOS return |
|---|---|---|---|---|
| W1 → W2 | 4/84 | 1.35 | 0.94 | +18.2% |
| W2 → W3 | 8/96 | 1.30 | 0.57 | +6.7% |
| W3 → W4 | 24/60 | 1.49 | 2.59 | +58.6% |
| W4 → W5 | 22/76 | 2.79 | -0.10 | -4.8% |
| W5 → W6 | 4/56 | 0.60 | -0.15 | -4.0% |
| W6 → W7 | 24/96 | 0.60 | 0.79 | +12.0% |
| W7 → W8 | 24/60 | 1.88 | 2.83 | +41.5% |

#### 4h · long_flat_gap_cd

| train → test | picked | in-sample Sharpe | OOS Sharpe | OOS return |
|---|---|---|---|---|
| W1 → W2 | 6/12 | -0.74 | 2.74 | +66.0% |
| W2 → W3 | 6/12 | 2.74 | 0.30 | +2.7% |
| W3 → W4 | 8/80 | 1.26 | 2.04 | +55.9% |
| W4 → W5 | 10/28 | 2.57 | 0.89 | +15.2% |
| W5 → W6 | 4/24 | 2.33 | -0.48 | -11.1% |
| W6 → W7 | 22/48 | 1.69 | -1.43 | -17.1% |
| W7 → W8 | 12/76 | -0.68 | 1.39 | +22.2% |

## Read before trusting

- Picking the best row of the summary table is itself in-sample selection across
  50 sweeps. The walk-forward numbers are the honest ones, and only per variant.
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
- `candidates.png` — whole-grid ensembles (full size and vol-targeted) vs buy & hold, 10 bps
