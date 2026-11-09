# Multi-year study — does any plateau show up?

BTC/USDT, Binance 15m klines 2017-08-17 → 2026-11-09 (323,626 bars),
resampled to 1h, 4h, 8h, 1d (bars stamped at close). Every variant is
swept over its whole grid on every bar size: 50 sweeps,
9,040 parameter sets. Every number is priced at **15 bps (10 fee + 5 slippage), next-open fills**
per unit of position change; 2 bps (VIP/maker) is shown only as an
optimistic reference. History split into 8 equal windows.

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
| W1 | 2017-08-17 → 2018-10-12 | 4,261 → 6,319 | +48.3% |
| W2 | 2018-10-12 → 2019-12-07 | 6,327 → 7,501 | +18.6% |
| W3 | 2019-12-07 → 2021-02-01 | 7,512 → 33,804 | +350.0% |
| W4 | 2021-02-01 → 2022-03-29 | 33,764 → 47,357 | +40.3% |
| W5 | 2022-03-29 → 2023-05-25 | 47,215 → 26,091 | -44.7% |
| W6 | 2023-05-25 → 2024-07-19 | 26,096 → 64,116 | +145.7% |
| W7 | 2024-07-19 → 2025-09-13 | 64,213 → 115,873 | +80.5% |
| W8 | 2025-09-13 → 2026-11-09 | 115,950 → 74,293 | -35.9% |

## Answer

- Plateaus at 15 bps in **23/50** sweeps:
  1h long_flat (62), 1h long_flat_gap_cd (69), 4h always_in (73), 4h gap_cooldown (60), 4h long_flat (172), 4h long_flat_gap_cd (146), 4h long_flat_adx (8), 8h always_in (94), 8h gap_cooldown (91), 8h flat_er (1), 8h flat_adx (13), 8h flat_adx_gap_cd (13), 8h long_flat (209), 8h long_flat_gap_cd (204), 8h long_flat_adx (51), 1d always_in (94), 1d gap_cooldown (118), 1d flat_er (22), 1d flat_adx (188), 1d flat_adx_gap_cd (193), 1d long_flat (201), 1d long_flat_gap_cd (201), 1d long_flat_adx (212).
- At an optimistic 2 bps, plateaus in **35** sweeps:
  15m long_flat (65), 15m long_flat_gap_cd (91), 15m long_flat_adx (79), 1h always_in (40), 1h gap_cooldown (61), 1h flat_adx (15), 1h flat_adx_gap_cd (54), 1h long_flat (202), 1h long_flat_gap_cd (203), 1h long_flat_adx (1), 4h always_in (130), 4h gap_cooldown (115), 4h flat_er (74), 4h flat_adx (28), 4h flat_adx_gap_cd (14), 4h long_flat (196), 4h long_flat_gap_cd (175), 4h long_flat_adx (155), 8h always_in (138), 8h gap_cooldown (129), 8h flat_er (211), 8h flat_adx (52), 8h flat_adx_gap_cd (52), 8h long_flat (212), 8h long_flat_gap_cd (208), 8h long_flat_adx (99), 8h mr_chop (1), 1d always_in (111), 1d gap_cooldown (134), 1d flat_er (86), 1d flat_adx (198), 1d flat_adx_gap_cd (198), 1d long_flat (201), 1d long_flat_gap_cd (202), 1d long_flat_adx (212).
- Walk-forward Sharpe above buy & hold over the same span in **10** sweeps:
  4h long_flat (0.82 vs 0.81), 4h long_flat_gap_cd (0.88 vs 0.81), 8h long_flat (1.17 vs 0.81), 8h long_flat_gap_cd (1.14 vs 0.81), 8h long_flat_adx (1.22 vs 0.81), 1d flat_adx (0.95 vs 0.81), 1d flat_adx_gap_cd (0.98 vs 0.81), 1d long_flat (1.12 vs 0.81), 1d long_flat_gap_cd (0.94 vs 0.81), 1d long_flat_adx (1.26 vs 0.81).

## Summary

| bars | variant | combos > 0 | plateaus (15 bps) | plateaus (2 bps) | best plateau | its Sharpe 15 / 2 bps | trades/yr | time in market | WF OOS Sharpe 15 / 2 bps | WF steps > 0 | WF OOS return / max DD | B&H OOS Sharpe | B&H OOS return / max DD |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 15m | always_in | 0/212 | **0** | 0 | - | – / – | 814 | 100% | -1.58 / 0.26 | 0/7 | -100.0% / -100% | 0.80 | +1075.7% / -77% |
| 15m | gap_cooldown | 0/212 | **0** | 0 | - | – / – | 1177 | 91% | -1.32 / 0.22 | 0/7 | -100.0% / -100% | 0.80 | +1075.7% / -77% |
| 15m | flat_er | 0/212 | **0** | 0 | - | – / – | 4099 | 25% | -15.06 / -1.73 | 0/7 | -100.0% / -100% | 0.80 | +1075.7% / -77% |
| 15m | flat_adx | 0/212 | **0** | 0 | - | – / – | 1320 | 45% | -3.45 / 0.30 | 0/7 | -100.0% / -100% | 0.80 | +1075.7% / -77% |
| 15m | flat_adx_gap_cd | 0/212 | **0** | 0 | - | – / – | 1292 | 43% | -3.21 / 0.26 | 0/7 | -100.0% / -100% | 0.80 | +1075.7% / -77% |
| 15m | long_flat | 0/212 | **0** | 65 | - | – / – | 814 | 52% | -0.59 / 0.80 | 1/7 | -93.7% / -96% | 0.80 | +1075.7% / -77% |
| 15m | long_flat_gap_cd | 0/212 | **0** | 91 | - | – / – | 620 | 49% | -0.38 / 0.69 | 1/7 | -86.4% / -93% | 0.80 | +1075.7% / -77% |
| 15m | long_flat_adx | 0/212 | **0** | 79 | - | – / – | 748 | 21% | -2.22 / 0.88 | 0/7 | -99.7% / -100% | 0.80 | +1075.7% / -77% |
| 15m | mr_always | 0/56 | **0** | 0 | - | – / – | 1442 | 47% | -2.56 / -0.65 | 0/7 | -96.5% / -97% | 0.80 | +1075.7% / -77% |
| 15m | mr_chop | 0/56 | **0** | 0 | - | – / – | 1174 | 9% | -2.02 / -0.48 | 0/7 | -66.3% / -66% | 0.80 | +1075.7% / -77% |
| 1h | always_in | 110/212 | **0** | 40 | - | – / – | 182 | 100% | -0.18 / 0.38 | 2/7 | -91.6% / -98% | 0.80 | +1075.0% / -77% |
| 1h | gap_cooldown | 143/212 | **0** | 61 | - | – / – | 268 | 92% | 0.11 / 0.45 | 3/7 | -59.8% / -91% | 0.80 | +1075.0% / -77% |
| 1h | flat_er | 0/212 | **0** | 0 | - | – / – | 1083 | 31% | -3.19 / 0.32 | 0/7 | -100.0% / -100% | 0.80 | +1075.0% / -77% |
| 1h | flat_adx | 0/212 | **0** | 15 | - | – / – | 345 | 51% | -0.35 / 0.68 | 1/7 | -90.6% / -96% | 0.80 | +1075.0% / -77% |
| 1h | flat_adx_gap_cd | 0/212 | **0** | 54 | - | – / – | 341 | 48% | -0.34 / 0.66 | 2/7 | -89.2% / -95% | 0.80 | +1075.0% / -77% |
| 1h | long_flat | 185/212 | **62** | 202 | 24/92 | 0.83 / 1.13 | 182 | 52% | 0.45 / 0.89 | 5/7 | +124.2% / -79% | 0.80 | +1075.0% / -77% |
| 1h | long_flat_gap_cd | 198/212 | **69** | 203 | 24/84 | 0.90 / 1.17 | 143 | 50% | 0.45 / 0.82 | 5/7 | +126.2% / -73% | 0.80 | +1075.0% / -77% |
| 1h | long_flat_adx | 32/212 | **0** | 1 | - | – / – | 205 | 24% | 0.08 / 0.89 | 3/7 | -16.4% / -70% | 0.80 | +1075.0% / -77% |
| 1h | mr_always | 0/56 | **0** | 0 | - | – / – | 356 | 50% | -1.76 / -0.56 | 0/7 | -99.5% / -100% | 0.80 | +1075.0% / -77% |
| 1h | mr_chop | 1/56 | **0** | 0 | - | – / – | 271 | 7% | -1.35 / -0.37 | 0/7 | -53.0% / -53% | 0.80 | +1075.0% / -77% |
| 4h | always_in | 212/212 | **73** | 130 | 14/88 | 0.72 / 0.83 | 47 | 99% | 0.27 / 0.47 | 4/7 | -12.8% / -76% | 0.81 | +1077.5% / -77% |
| 4h | gap_cooldown | 212/212 | **60** | 115 | 8/96 | 0.74 / 0.86 | 68 | 91% | 0.46 / 0.57 | 4/7 | +124.8% / -73% | 0.81 | +1077.5% / -77% |
| 4h | flat_er | 0/212 | **0** | 74 | - | – / – | 274 | 35% | -0.46 / 0.57 | 1/7 | -86.9% / -96% | 0.81 | +1077.5% / -77% |
| 4h | flat_adx | 187/212 | **0** | 28 | - | – / – | 90 | 52% | -0.04 / 0.29 | 2/7 | -65.9% / -85% | 0.81 | +1077.5% / -77% |
| 4h | flat_adx_gap_cd | 202/212 | **0** | 14 | - | – / – | 91 | 50% | 0.03 / 0.22 | 2/7 | -53.0% / -83% | 0.81 | +1077.5% / -77% |
| 4h | long_flat | 212/212 | **172** | 196 | 16/80 | 1.13 / 1.22 | 47 | 52% | 0.82 / 0.99 | 5/7 | +662.8% / -59% | 0.81 | +1077.5% / -77% |
| 4h | long_flat_gap_cd | 212/212 | **146** | 175 | 12/80 | 1.18 / 1.27 | 36 | 50% | 0.88 / 1.05 | 4/7 | +776.1% / -54% | 0.81 | +1077.5% / -77% |
| 4h | long_flat_adx | 212/212 | **8** | 155 | 10/88 | 0.70 / 0.89 | 52 | 26% | 0.55 / 0.85 | 4/7 | +163.6% / -54% | 0.81 | +1077.5% / -77% |
| 4h | mr_always | 0/56 | **0** | 0 | - | – / – | 82 | 49% | -0.46 / -0.47 | 1/7 | -81.4% / -89% | 0.81 | +1077.5% / -77% |
| 4h | mr_chop | 0/56 | **0** | 0 | - | – / – | 58 | 5% | -0.79 / -0.31 | 2/7 | -52.1% / -57% | 0.81 | +1077.5% / -77% |
| 8h | always_in | 212/212 | **94** | 138 | 24/96 | 0.90 / 0.95 | 22 | 99% | 0.80 / 0.94 | 5/7 | +1008.0% / -61% | 0.81 | +1073.2% / -77% |
| 8h | gap_cooldown | 212/212 | **91** | 129 | 14/96 | 0.84 / 0.89 | 34 | 91% | 0.70 / 0.85 | 4/7 | +570.7% / -58% | 0.81 | +1073.2% / -77% |
| 8h | flat_er | 212/212 | **1** | 211 | 24/96 | 0.62 / 1.02 | 130 | 35% | 0.77 / 1.20 | 6/7 | +538.5% / -62% | 0.81 | +1073.2% / -77% |
| 8h | flat_adx | 212/212 | **13** | 52 | 4/44 | 0.54 / 0.68 | 43 | 53% | 0.60 / 0.74 | 3/7 | +309.6% / -67% | 0.81 | +1073.2% / -77% |
| 8h | flat_adx_gap_cd | 212/212 | **13** | 52 | 18/68 | 0.65 / 0.76 | 44 | 50% | 0.46 / 0.60 | 4/7 | +140.3% / -69% | 0.81 | +1073.2% / -77% |
| 8h | long_flat | 212/212 | **209** | 212 | 24/96 | 1.22 / 1.25 | 22 | 52% | 1.17 / 1.23 | 5/7 | +2275.6% / -51% | 0.81 | +1073.2% / -77% |
| 8h | long_flat_gap_cd | 212/212 | **204** | 208 | 24/96 | 1.25 / 1.27 | 18 | 49% | 1.14 / 1.23 | 6/7 | +1903.0% / -56% | 0.81 | +1073.2% / -77% |
| 8h | long_flat_adx | 212/212 | **51** | 99 | 24/96 | 1.12 / 1.21 | 26 | 28% | 1.22 / 1.35 | 5/7 | +1511.9% / -49% | 0.81 | +1073.2% / -77% |
| 8h | mr_always | 0/56 | **0** | 0 | - | – / – | 43 | 49% | -0.62 / -0.53 | 1/7 | -88.7% / -90% | 0.81 | +1073.2% / -77% |
| 8h | mr_chop | 17/56 | **0** | 1 | - | – / – | 29 | 6% | 0.10 / -0.03 | 5/7 | +3.3% / -22% | 0.81 | +1073.2% / -77% |
| 1d | always_in | 212/212 | **94** | 111 | 10/32 | 0.89 / 0.93 | 7 | 95% | 0.78 / 0.84 | 6/7 | +944.7% / -65% | 0.81 | +1079.6% / -77% |
| 1d | gap_cooldown | 212/212 | **118** | 134 | 4/36 | 0.83 / 0.87 | 10 | 91% | 0.45 / 0.50 | 4/7 | +119.7% / -74% | 0.81 | +1079.6% / -77% |
| 1d | flat_er | 212/212 | **22** | 86 | 24/40 | 0.92 / 1.05 | 43 | 35% | 0.66 / 0.80 | 4/7 | +382.7% / -65% | 0.81 | +1079.6% / -77% |
| 1d | flat_adx | 212/212 | **188** | 198 | 22/64 | 0.94 / 0.98 | 15 | 55% | 0.95 / 0.99 | 6/7 | +1618.8% / -45% | 0.81 | +1079.6% / -77% |
| 1d | flat_adx_gap_cd | 212/212 | **193** | 198 | 6/84 | 0.92 / 0.96 | 15 | 53% | 0.98 / 1.02 | 5/7 | +1773.7% / -39% | 0.81 | +1079.6% / -77% |
| 1d | long_flat | 212/212 | **201** | 201 | 10/32 | 1.20 / 1.23 | 7 | 52% | 1.12 / 1.16 | 5/7 | +2319.6% / -55% | 0.81 | +1079.6% / -77% |
| 1d | long_flat_gap_cd | 212/212 | **201** | 202 | 6/36 | 1.21 / 1.24 | 6 | 50% | 0.94 / 0.97 | 5/7 | +1085.8% / -53% | 0.81 | +1079.6% / -77% |
| 1d | long_flat_adx | 212/212 | **212** | 212 | 10/88 | 1.32 / 1.35 | 9 | 30% | 1.26 / 1.35 | 6/7 | +1976.7% / -36% | 0.81 | +1079.6% / -77% |
| 1d | mr_always | 0/56 | **0** | 0 | - | – / – | 12 | 53% | -0.57 / -0.49 | 1/7 | -92.1% / -93% | 0.81 | +1079.6% / -77% |
| 1d | mr_chop | 18/56 | **0** | 0 | - | – / – | 7 | 4% | -0.23 / -0.04 | 2/7 | -47.7% / -65% | 0.81 | +1079.6% / -77% |

`trades/yr` and `time in market` are medians over the grid. Full per-combo tables are in
`sweeps/` and in ArcticDB (`BTCUSDT_<bars>_<variant>_history_sweep`).

## Robust construction — no parameter is picked anywhere

Instead of choosing one combo, each trend variant trades its **whole grid at once**: exposure is
the average position of all 212 combos, netted into one book (fees on the net change). Each book
is shown at full size and **vol-targeted** (exposure × min(1, 40% /
30-day realised vol), re-set daily, no leverage). Buy & hold gets the same
vol-targeting, so every candidate is compared against the benchmark sized the same way.

**Pass rule (fixed before looking at the results):** on W2–W8 at 15 bps (10 fee + 5 slippage), next-open fills,
Sharpe above *and* max drawdown shallower than the matching buy & hold.
**Robust** = passes on at least two *adjacent* bar sizes
(15m → 1h → 4h → 8h → 1d), so one lucky bar size is not enough.

**Passes at 15 bps (31):** 4h ensemble long_flat (full): Sharpe 0.87 vs 0.81, max DD -63% vs -77%, 4h ensemble long_flat (vol-target): Sharpe 1.09 vs 0.80, max DD -44% vs -66%, 4h ensemble long_flat_gap_cd (full): Sharpe 0.93 vs 0.81, max DD -60% vs -77%, 4h ensemble long_flat_gap_cd (vol-target): Sharpe 1.16 vs 0.80, max DD -43% vs -66%, 4h ensemble long_flat_adx (vol-target): Sharpe 0.87 vs 0.80, max DD -42% vs -66%, 8h ensemble always_in (vol-target): Sharpe 1.04 vs 0.78, max DD -40% vs -66%, 8h ensemble gap_cooldown (vol-target): Sharpe 1.04 vs 0.78, max DD -46% vs -66%, 8h ensemble flat_er (full): Sharpe 0.82 vs 0.81, max DD -54% vs -77%, 8h ensemble flat_er (vol-target): Sharpe 1.06 vs 0.78, max DD -36% vs -66%, 8h ensemble flat_adx (vol-target): Sharpe 0.83 vs 0.78, max DD -40% vs -66%, 8h ensemble flat_adx_gap_cd (vol-target): Sharpe 0.83 vs 0.78, max DD -41% vs -66%, 8h ensemble long_flat (full): Sharpe 1.13 vs 0.81, max DD -53% vs -77%, 8h ensemble long_flat (vol-target): Sharpe 1.31 vs 0.78, max DD -41% vs -66%, 8h ensemble long_flat_gap_cd (full): Sharpe 1.16 vs 0.81, max DD -53% vs -77%, 8h ensemble long_flat_gap_cd (vol-target): Sharpe 1.33 vs 0.78, max DD -42% vs -66%, 8h ensemble long_flat_adx (full): Sharpe 1.27 vs 0.81, max DD -50% vs -77%, 8h ensemble long_flat_adx (vol-target): Sharpe 1.32 vs 0.78, max DD -39% vs -66%, 1d ensemble always_in (full): Sharpe 0.81 vs 0.81, max DD -52% vs -77%, 1d ensemble always_in (vol-target): Sharpe 0.87 vs 0.78, max DD -45% vs -65%, 1d ensemble gap_cooldown (vol-target): Sharpe 0.81 vs 0.78, max DD -45% vs -65%, 1d ensemble flat_er (vol-target): Sharpe 0.94 vs 0.78, max DD -38% vs -65%, 1d ensemble flat_adx (full): Sharpe 0.96 vs 0.81, max DD -38% vs -77%, 1d ensemble flat_adx (vol-target): Sharpe 1.14 vs 0.78, max DD -28% vs -65%, 1d ensemble flat_adx_gap_cd (full): Sharpe 0.95 vs 0.81, max DD -38% vs -77%, 1d ensemble flat_adx_gap_cd (vol-target): Sharpe 1.13 vs 0.78, max DD -26% vs -65%, 1d ensemble long_flat (full): Sharpe 1.15 vs 0.81, max DD -45% vs -77%, 1d ensemble long_flat (vol-target): Sharpe 1.15 vs 0.78, max DD -38% vs -65%, 1d ensemble long_flat_gap_cd (full): Sharpe 1.14 vs 0.81, max DD -43% vs -77%, 1d ensemble long_flat_gap_cd (vol-target): Sharpe 1.13 vs 0.78, max DD -36% vs -65%, 1d ensemble long_flat_adx (full): Sharpe 1.36 vs 0.81, max DD -28% vs -77%, 1d ensemble long_flat_adx (vol-target): Sharpe 1.44 vs 0.78, max DD -27% vs -65%.
At an optimistic 2 bps: 44 pass.

| book | sizing | passes on | verdict |
|---|---|---|---|
| ensemble long_flat | full | 4h, 8h, 1d | **robust** |
| ensemble long_flat | vol-target | 4h, 8h, 1d | **robust** |
| ensemble long_flat_adx | vol-target | 4h, 8h, 1d | **robust** |
| ensemble long_flat_gap_cd | full | 4h, 8h, 1d | **robust** |
| ensemble long_flat_gap_cd | vol-target | 4h, 8h, 1d | **robust** |
| ensemble always_in | vol-target | 8h, 1d | **robust** |
| ensemble flat_adx | vol-target | 8h, 1d | **robust** |
| ensemble flat_adx_gap_cd | vol-target | 8h, 1d | **robust** |
| ensemble flat_er | vol-target | 8h, 1d | **robust** |
| ensemble gap_cooldown | vol-target | 8h, 1d | **robust** |
| ensemble long_flat_adx | full | 8h, 1d | **robust** |
| ensemble always_in | full | 1d | single bar size only |
| ensemble flat_adx | full | 1d | single bar size only |
| ensemble flat_adx_gap_cd | full | 1d | single bar size only |
| ensemble flat_er | full | 8h | single bar size only |

Main books at 15 bps (all 8 variants in
`candidates.csv`):

| bars | book | sizing | Sharpe | vs bench | CAGR | max DD | vs bench | windows > 0 | avg exposure | turnover/yr | passes |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 15m | buy & hold | full | 0.80 | 0.80 | +35.7% | -77% | -77% | 5/7 | 100% | 0× | bench |
| 15m | buy & hold | vol-target | 0.80 | 0.80 | +27.8% | -66% | -66% | 5/7 | 74% | 4× | bench |
| 15m | ensemble always_in | full | -4.61 | 0.80 | -94.2% | -100% | -77% | 0/7 | 84% | 1941× | no |
| 15m | ensemble always_in | vol-target | -5.03 | 0.80 | -86.0% | -100% | -66% | 0/7 | 63% | 1449× | no |
| 15m | ensemble long_flat | full | -2.69 | 0.80 | -68.8% | -100% | -77% | 0/7 | 52% | 971× | no |
| 15m | ensemble long_flat | vol-target | -2.90 | 0.80 | -55.8% | -100% | -66% | 0/7 | 39% | 725× | no |
| 15m | ensemble long_flat_gap_cd | full | -1.89 | 0.80 | -55.2% | -100% | -77% | 0/7 | 47% | 721× | no |
| 15m | ensemble long_flat_gap_cd | vol-target | -1.98 | 0.80 | -42.0% | -99% | -66% | 0/7 | 35% | 539× | no |
| 15m | ensemble long_flat_adx | full | -2.79 | 0.80 | -58.3% | -100% | -77% | 0/7 | 21% | 784× | no |
| 15m | ensemble long_flat_adx | vol-target | -2.99 | 0.80 | -47.0% | -99% | -66% | 0/7 | 16% | 587× | no |
| 1h | buy & hold | full | 0.80 | 0.80 | +35.6% | -77% | -77% | 5/7 | 100% | 0× | bench |
| 1h | buy & hold | vol-target | 0.80 | 0.80 | +27.8% | -67% | -67% | 5/7 | 75% | 4× | bench |
| 1h | ensemble always_in | full | -0.52 | 0.80 | -36.4% | -99% | -77% | 2/7 | 85% | 441× | no |
| 1h | ensemble always_in | vol-target | -0.41 | 0.80 | -20.3% | -94% | -67% | 2/7 | 64% | 336× | no |
| 1h | ensemble long_flat | full | 0.26 | 0.80 | +2.4% | -81% | -77% | 4/7 | 52% | 221× | no |
| 1h | ensemble long_flat | vol-target | 0.33 | 0.80 | +5.4% | -64% | -67% | 5/7 | 39% | 168× | no |
| 1h | ensemble long_flat_gap_cd | full | 0.44 | 0.80 | +10.0% | -72% | -77% | 4/7 | 48% | 166× | no |
| 1h | ensemble long_flat_gap_cd | vol-target | 0.53 | 0.80 | +11.2% | -55% | -67% | 5/7 | 36% | 127× | no |
| 1h | ensemble long_flat_adx | full | -0.16 | 0.80 | -8.8% | -80% | -77% | 3/7 | 24% | 215× | no |
| 1h | ensemble long_flat_adx | vol-target | -0.14 | 0.80 | -5.1% | -63% | -67% | 3/7 | 18% | 163× | no |
| 4h | buy & hold | full | 0.81 | 0.81 | +35.7% | -77% | -77% | 5/7 | 100% | 0× | bench |
| 4h | buy & hold | vol-target | 0.80 | 0.80 | +27.4% | -66% | -66% | 5/7 | 76% | 4× | bench |
| 4h | ensemble always_in | full | 0.35 | 0.81 | +4.2% | -73% | -77% | 3/7 | 85% | 108× | no |
| 4h | ensemble always_in | vol-target | 0.70 | 0.80 | +21.2% | -43% | -66% | 5/7 | 65% | 84× | no |
| 4h | ensemble long_flat | full | 0.87 | 0.81 | +30.0% | -63% | -77% | 5/7 | 53% | 54× | **YES** |
| 4h | ensemble long_flat | vol-target | 1.09 | 0.80 | +29.7% | -44% | -66% | 5/7 | 41% | 42× | **YES** |
| 4h | ensemble long_flat_gap_cd | full | 0.93 | 0.81 | +31.8% | -60% | -77% | 5/7 | 49% | 42× | **YES** |
| 4h | ensemble long_flat_gap_cd | vol-target | 1.16 | 0.80 | +30.9% | -43% | -66% | 5/7 | 38% | 33× | **YES** |
| 4h | ensemble long_flat_adx | full | 0.70 | 0.81 | +17.6% | -55% | -77% | 3/7 | 27% | 54× | no |
| 4h | ensemble long_flat_adx | vol-target | 0.87 | 0.80 | +17.6% | -42% | -66% | 4/7 | 21% | 42× | **YES** |
| 8h | buy & hold | full | 0.81 | 0.81 | +35.6% | -77% | -77% | 5/7 | 100% | 0× | bench |
| 8h | buy & hold | vol-target | 0.78 | 0.78 | +26.9% | -66% | -66% | 5/7 | 77% | 4× | bench |
| 8h | ensemble always_in | full | 0.72 | 0.81 | +27.6% | -54% | -77% | 5/7 | 85% | 52× | no |
| 8h | ensemble always_in | vol-target | 1.04 | 0.78 | +38.0% | -40% | -66% | 6/7 | 66% | 43× | **YES** |
| 8h | ensemble long_flat | full | 1.13 | 0.81 | +43.3% | -53% | -77% | 5/7 | 53% | 26× | **YES** |
| 8h | ensemble long_flat | vol-target | 1.31 | 0.78 | +38.2% | -41% | -66% | 5/7 | 42% | 21× | **YES** |
| 8h | ensemble long_flat_gap_cd | full | 1.16 | 0.81 | +43.5% | -53% | -77% | 5/7 | 50% | 20× | **YES** |
| 8h | ensemble long_flat_gap_cd | vol-target | 1.33 | 0.78 | +38.0% | -42% | -66% | 5/7 | 39% | 17× | **YES** |
| 8h | ensemble long_flat_adx | full | 1.27 | 0.81 | +41.0% | -50% | -77% | 5/7 | 29% | 26× | **YES** |
| 8h | ensemble long_flat_adx | vol-target | 1.32 | 0.78 | +30.9% | -39% | -66% | 5/7 | 23% | 22× | **YES** |
| 1d | buy & hold | full | 0.81 | 0.81 | +35.7% | -77% | -77% | 5/7 | 100% | 0× | bench |
| 1d | buy & hold | vol-target | 0.78 | 0.78 | +27.2% | -65% | -65% | 5/7 | 75% | 5× | bench |
| 1d | ensemble always_in | full | 0.81 | 0.81 | +35.0% | -52% | -77% | 7/7 | 84% | 16× | **YES** |
| 1d | ensemble always_in | vol-target | 0.87 | 0.78 | +29.9% | -45% | -65% | 6/7 | 63% | 16× | **YES** |
| 1d | ensemble long_flat | full | 1.15 | 0.81 | +48.0% | -45% | -77% | 5/7 | 55% | 8× | **YES** |
| 1d | ensemble long_flat | vol-target | 1.15 | 0.78 | +34.2% | -38% | -65% | 5/7 | 42% | 8× | **YES** |
| 1d | ensemble long_flat_gap_cd | full | 1.14 | 0.81 | +46.1% | -43% | -77% | 5/7 | 52% | 6× | **YES** |
| 1d | ensemble long_flat_gap_cd | vol-target | 1.13 | 0.78 | +32.6% | -36% | -65% | 5/7 | 40% | 7× | **YES** |
| 1d | ensemble long_flat_adx | full | 1.36 | 0.81 | +49.4% | -28% | -77% | 6/7 | 31% | 9× | **YES** |
| 1d | ensemble long_flat_adx | vol-target | 1.44 | 0.78 | +35.6% | -27% | -65% | 6/7 | 23% | 8× | **YES** |

The choice of *which* variant to trade is still made after seeing this table; with
8 variants × 2 sizings × 5 bar sizes, expect
a few to pass by luck. The long/flat variants were also added *after* seeing that shorting
BTC lost money, which is hindsight. The only fully clean test left is new data: paper trade.


## Plateaus

### 1h · long_flat — 62 plateaus (top 5)

| fast/slow | Sharpe 15 bps | Sharpe 2 bps | return | max DD | windows > 0 | neighbours' median Sharpe | time in market |
|---|---|---|---|---|---|---|---|
| 24/92 | 0.83 | 1.13 | +1194.4% | -66% | 6/8 | 0.77 | 52% |
| 24/96 | 0.83 | 1.12 | +1182.0% | -67% | 6/8 | 0.77 | 52% |
| 12/92 | 0.83 | 1.23 | +1165.0% | -67% | 6/8 | 0.73 | 52% |
| 24/84 | 0.81 | 1.13 | +1077.8% | -67% | 6/8 | 0.78 | 52% |
| 16/80 | 0.81 | 1.18 | +1074.9% | -68% | 6/8 | 0.76 | 52% |

### 1h · long_flat_gap_cd — 69 plateaus (top 5)

| fast/slow | Sharpe 15 bps | Sharpe 2 bps | return | max DD | windows > 0 | neighbours' median Sharpe | time in market |
|---|---|---|---|---|---|---|---|
| 24/84 | 0.90 | 1.17 | +1554.5% | -62% | 6/8 | 0.84 | 50% |
| 24/92 | 0.89 | 1.14 | +1467.6% | -64% | 6/8 | 0.87 | 50% |
| 22/92 | 0.88 | 1.14 | +1398.5% | -64% | 6/8 | 0.85 | 50% |
| 12/80 | 0.87 | 1.23 | +1387.6% | -62% | 6/8 | 0.81 | 50% |
| 24/88 | 0.87 | 1.13 | +1362.6% | -63% | 6/8 | 0.88 | 50% |

### 4h · always_in — 73 plateaus (top 5)

| fast/slow | Sharpe 15 bps | Sharpe 2 bps | return | max DD | windows > 0 | neighbours' median Sharpe | time in market |
|---|---|---|---|---|---|---|---|
| 14/88 | 0.72 | 0.83 | +944.5% | -67% | 7/8 | 0.68 | 99% |
| 16/80 | 0.72 | 0.83 | +943.7% | -67% | 7/8 | 0.67 | 99% |
| 10/92 | 0.71 | 0.85 | +920.5% | -70% | 6/8 | 0.67 | 99% |
| 14/84 | 0.71 | 0.83 | +908.5% | -69% | 7/8 | 0.68 | 99% |
| 10/88 | 0.71 | 0.85 | +892.0% | -71% | 6/8 | 0.66 | 99% |

### 4h · gap_cooldown — 60 plateaus (top 5)

| fast/slow | Sharpe 15 bps | Sharpe 2 bps | return | max DD | windows > 0 | neighbours' median Sharpe | time in market |
|---|---|---|---|---|---|---|---|
| 8/96 | 0.74 | 0.86 | +1094.1% | -54% | 6/8 | 0.62 | 94% |
| 14/84 | 0.68 | 0.79 | +726.0% | -64% | 6/8 | 0.63 | 94% |
| 8/92 | 0.68 | 0.81 | +717.2% | -59% | 7/8 | 0.62 | 94% |
| 14/88 | 0.67 | 0.78 | +696.9% | -59% | 6/8 | 0.63 | 94% |
| 14/80 | 0.67 | 0.78 | +665.6% | -69% | 6/8 | 0.64 | 94% |

### 4h · long_flat — 172 plateaus (top 5)

| fast/slow | Sharpe 15 bps | Sharpe 2 bps | return | max DD | windows > 0 | neighbours' median Sharpe | time in market |
|---|---|---|---|---|---|---|---|
| 16/80 | 1.13 | 1.22 | +4033.5% | -56% | 6/8 | 1.10 | 52% |
| 14/88 | 1.13 | 1.22 | +3918.3% | -55% | 6/8 | 1.10 | 52% |
| 14/84 | 1.13 | 1.22 | +3910.7% | -58% | 7/8 | 1.11 | 52% |
| 16/84 | 1.12 | 1.21 | +3867.9% | -59% | 6/8 | 1.09 | 52% |
| 10/88 | 1.12 | 1.23 | +3862.6% | -60% | 6/8 | 1.08 | 52% |

### 4h · long_flat_gap_cd — 146 plateaus (top 5)

| fast/slow | Sharpe 15 bps | Sharpe 2 bps | return | max DD | windows > 0 | neighbours' median Sharpe | time in market |
|---|---|---|---|---|---|---|---|
| 12/80 | 1.18 | 1.27 | +4625.9% | -58% | 7/8 | 1.12 | 51% |
| 6/96 | 1.17 | 1.28 | +4597.2% | -54% | 6/8 | 1.10 | 51% |
| 8/96 | 1.16 | 1.26 | +4446.4% | -53% | 6/8 | 1.11 | 51% |
| 10/88 | 1.16 | 1.25 | +4264.6% | -57% | 6/8 | 1.10 | 51% |
| 12/76 | 1.15 | 1.25 | +4134.5% | -56% | 7/8 | 1.14 | 50% |

### 4h · long_flat_adx — 8 plateaus (top 5)

| fast/slow | Sharpe 15 bps | Sharpe 2 bps | return | max DD | windows > 0 | neighbours' median Sharpe | time in market |
|---|---|---|---|---|---|---|---|
| 10/88 | 0.70 | 0.89 | +439.6% | -52% | 6/8 | 0.66 | 27% |
| 8/92 | 0.69 | 0.88 | +417.2% | -53% | 6/8 | 0.66 | 27% |
| 16/72 | 0.64 | 0.83 | +336.9% | -54% | 6/8 | 0.62 | 27% |
| 22/48 | 0.63 | 0.83 | +329.4% | -52% | 6/8 | 0.61 | 26% |
| 18/64 | 0.63 | 0.82 | +325.2% | -54% | 6/8 | 0.62 | 26% |

### 8h · always_in — 94 plateaus (top 5)

| fast/slow | Sharpe 15 bps | Sharpe 2 bps | return | max DD | windows > 0 | neighbours' median Sharpe | time in market |
|---|---|---|---|---|---|---|---|
| 24/96 | 0.90 | 0.95 | +3013.3% | -79% | 7/8 | 0.83 | 99% |
| 24/92 | 0.87 | 0.91 | +2470.7% | -77% | 7/8 | 0.82 | 99% |
| 24/76 | 0.83 | 0.88 | +1944.0% | -74% | 7/8 | 0.74 | 99% |
| 22/96 | 0.83 | 0.87 | +1906.2% | -81% | 7/8 | 0.80 | 99% |
| 24/88 | 0.82 | 0.87 | +1815.4% | -78% | 7/8 | 0.79 | 99% |

### 8h · gap_cooldown — 91 plateaus (top 5)

| fast/slow | Sharpe 15 bps | Sharpe 2 bps | return | max DD | windows > 0 | neighbours' median Sharpe | time in market |
|---|---|---|---|---|---|---|---|
| 14/96 | 0.84 | 0.89 | +2029.6% | -76% | 7/8 | 0.80 | 94% |
| 14/92 | 0.84 | 0.89 | +1989.6% | -73% | 7/8 | 0.79 | 93% |
| 18/88 | 0.82 | 0.87 | +1840.7% | -73% | 7/8 | 0.81 | 94% |
| 6/96 | 0.82 | 0.89 | +1838.4% | -78% | 7/8 | 0.72 | 94% |
| 10/96 | 0.82 | 0.88 | +1795.7% | -77% | 7/8 | 0.78 | 94% |

### 8h · flat_er — 1 plateaus (top 5)

| fast/slow | Sharpe 15 bps | Sharpe 2 bps | return | max DD | windows > 0 | neighbours' median Sharpe | time in market |
|---|---|---|---|---|---|---|---|
| 24/96 | 0.62 | 1.02 | +411.5% | -68% | 6/8 | 0.50 | 35% |

### 8h · flat_adx — 13 plateaus (top 5)

| fast/slow | Sharpe 15 bps | Sharpe 2 bps | return | max DD | windows > 0 | neighbours' median Sharpe | time in market |
|---|---|---|---|---|---|---|---|
| 4/44 | 0.54 | 0.68 | +289.3% | -58% | 6/8 | 0.44 | 53% |
| 4/48 | 0.53 | 0.66 | +271.0% | -62% | 6/8 | 0.50 | 53% |
| 4/88 | 0.51 | 0.64 | +232.2% | -78% | 6/8 | 0.43 | 52% |
| 4/80 | 0.50 | 0.63 | +211.1% | -71% | 6/8 | 0.47 | 52% |
| 4/96 | 0.48 | 0.61 | +188.9% | -79% | 6/8 | 0.44 | 52% |

### 8h · flat_adx_gap_cd — 13 plateaus (top 5)

| fast/slow | Sharpe 15 bps | Sharpe 2 bps | return | max DD | windows > 0 | neighbours' median Sharpe | time in market |
|---|---|---|---|---|---|---|---|
| 18/68 | 0.65 | 0.76 | +550.1% | -62% | 6/8 | 0.59 | 50% |
| 6/76 | 0.56 | 0.67 | +324.9% | -73% | 6/8 | 0.52 | 51% |
| 4/44 | 0.54 | 0.66 | +283.7% | -53% | 6/8 | 0.47 | 50% |
| 4/96 | 0.50 | 0.62 | +217.8% | -75% | 6/8 | 0.51 | 50% |
| 4/48 | 0.50 | 0.61 | +210.4% | -62% | 6/8 | 0.48 | 51% |

### 8h · long_flat — 209 plateaus (top 5)

| fast/slow | Sharpe 15 bps | Sharpe 2 bps | return | max DD | windows > 0 | neighbours' median Sharpe | time in market |
|---|---|---|---|---|---|---|---|
| 24/96 | 1.22 | 1.25 | +6126.3% | -63% | 6/8 | 1.17 | 52% |
| 24/92 | 1.20 | 1.23 | +5589.5% | -63% | 6/8 | 1.16 | 52% |
| 24/76 | 1.18 | 1.22 | +5069.5% | -66% | 6/8 | 1.12 | 52% |
| 22/96 | 1.17 | 1.20 | +4937.4% | -65% | 6/8 | 1.15 | 52% |
| 20/72 | 1.16 | 1.21 | +4739.7% | -72% | 7/8 | 1.13 | 52% |

### 8h · long_flat_gap_cd — 204 plateaus (top 5)

| fast/slow | Sharpe 15 bps | Sharpe 2 bps | return | max DD | windows > 0 | neighbours' median Sharpe | time in market |
|---|---|---|---|---|---|---|---|
| 24/96 | 1.25 | 1.27 | +6674.2% | -60% | 6/8 | 1.18 | 50% |
| 24/92 | 1.22 | 1.25 | +6003.9% | -58% | 6/8 | 1.15 | 50% |
| 6/96 | 1.21 | 1.26 | +5507.1% | -70% | 7/8 | 1.14 | 51% |
| 6/36 | 1.21 | 1.30 | +4646.8% | -58% | 6/8 | 1.11 | 49% |
| 18/68 | 1.20 | 1.24 | +5027.3% | -74% | 7/8 | 1.09 | 49% |

### 8h · long_flat_adx — 51 plateaus (top 5)

| fast/slow | Sharpe 15 bps | Sharpe 2 bps | return | max DD | windows > 0 | neighbours' median Sharpe | time in market |
|---|---|---|---|---|---|---|---|
| 24/96 | 1.12 | 1.21 | +2378.3% | -57% | 6/8 | 1.05 | 30% |
| 6/52 | 1.10 | 1.20 | +1896.9% | -57% | 6/8 | 1.06 | 28% |
| 6/68 | 1.10 | 1.20 | +1917.9% | -60% | 6/8 | 1.08 | 28% |
| 4/48 | 1.10 | 1.21 | +1809.2% | -52% | 6/8 | 1.06 | 28% |
| 6/60 | 1.10 | 1.19 | +1866.4% | -60% | 6/8 | 1.07 | 28% |

### 1d · always_in — 94 plateaus (top 5)

| fast/slow | Sharpe 15 bps | Sharpe 2 bps | return | max DD | windows > 0 | neighbours' median Sharpe | time in market |
|---|---|---|---|---|---|---|---|
| 10/32 | 0.89 | 0.93 | +2896.3% | -73% | 6/8 | 0.80 | 99% |
| 24/40 | 0.88 | 0.90 | +2707.7% | -70% | 6/8 | 0.78 | 99% |
| 8/32 | 0.86 | 0.91 | +2495.1% | -70% | 6/8 | 0.79 | 99% |
| 8/36 | 0.84 | 0.88 | +2184.3% | -72% | 7/8 | 0.79 | 99% |
| 6/40 | 0.84 | 0.88 | +2128.3% | -76% | 6/8 | 0.75 | 99% |

### 1d · gap_cooldown — 118 plateaus (top 5)

| fast/slow | Sharpe 15 bps | Sharpe 2 bps | return | max DD | windows > 0 | neighbours' median Sharpe | time in market |
|---|---|---|---|---|---|---|---|
| 4/36 | 0.83 | 0.87 | +1892.0% | -72% | 7/8 | 0.68 | 87% |
| 4/32 | 0.82 | 0.87 | +1800.3% | -69% | 6/8 | 0.70 | 87% |
| 4/40 | 0.79 | 0.83 | +1498.8% | -73% | 7/8 | 0.68 | 88% |
| 4/64 | 0.78 | 0.81 | +1443.2% | -68% | 7/8 | 0.73 | 91% |
| 4/60 | 0.78 | 0.81 | +1438.1% | -68% | 8/8 | 0.71 | 91% |

### 1d · flat_er — 22 plateaus (top 5)

| fast/slow | Sharpe 15 bps | Sharpe 2 bps | return | max DD | windows > 0 | neighbours' median Sharpe | time in market |
|---|---|---|---|---|---|---|---|
| 24/40 | 0.92 | 1.05 | +1743.9% | -46% | 6/8 | 0.77 | 35% |
| 18/68 | 0.87 | 0.99 | +1331.6% | -50% | 6/8 | 0.79 | 35% |
| 16/72 | 0.86 | 0.99 | +1286.5% | -52% | 6/8 | 0.78 | 35% |
| 22/44 | 0.82 | 0.95 | +1111.3% | -49% | 6/8 | 0.76 | 35% |
| 14/76 | 0.80 | 0.93 | +991.4% | -54% | 6/8 | 0.70 | 35% |

### 1d · flat_adx — 188 plateaus (top 5)

| fast/slow | Sharpe 15 bps | Sharpe 2 bps | return | max DD | windows > 0 | neighbours' median Sharpe | time in market |
|---|---|---|---|---|---|---|---|
| 22/64 | 0.94 | 0.98 | +2897.6% | -71% | 7/8 | 0.89 | 55% |
| 8/92 | 0.94 | 0.98 | +2801.9% | -67% | 7/8 | 0.90 | 55% |
| 8/84 | 0.94 | 0.98 | +2808.6% | -60% | 8/8 | 0.90 | 55% |
| 10/88 | 0.93 | 0.97 | +2698.2% | -68% | 8/8 | 0.90 | 55% |
| 6/92 | 0.93 | 0.97 | +2669.1% | -60% | 8/8 | 0.90 | 55% |

### 1d · flat_adx_gap_cd — 193 plateaus (top 5)

| fast/slow | Sharpe 15 bps | Sharpe 2 bps | return | max DD | windows > 0 | neighbours' median Sharpe | time in market |
|---|---|---|---|---|---|---|---|
| 6/84 | 0.92 | 0.96 | +2458.2% | -62% | 8/8 | 0.86 | 53% |
| 12/88 | 0.92 | 0.96 | +2441.5% | -68% | 8/8 | 0.86 | 53% |
| 6/92 | 0.91 | 0.95 | +2379.7% | -62% | 7/8 | 0.87 | 53% |
| 6/88 | 0.90 | 0.94 | +2233.1% | -65% | 8/8 | 0.87 | 53% |
| 8/88 | 0.89 | 0.93 | +2164.0% | -67% | 7/8 | 0.89 | 53% |

### 1d · long_flat — 201 plateaus (top 5)

| fast/slow | Sharpe 15 bps | Sharpe 2 bps | return | max DD | windows > 0 | neighbours' median Sharpe | time in market |
|---|---|---|---|---|---|---|---|
| 10/32 | 1.20 | 1.23 | +6078.4% | -59% | 6/8 | 1.13 | 51% |
| 8/32 | 1.18 | 1.21 | +5622.3% | -62% | 6/8 | 1.13 | 52% |
| 8/36 | 1.17 | 1.20 | +5312.6% | -58% | 6/8 | 1.13 | 51% |
| 24/40 | 1.17 | 1.18 | +5632.5% | -63% | 6/8 | 1.11 | 52% |
| 6/40 | 1.16 | 1.19 | +5211.3% | -61% | 6/8 | 1.11 | 51% |

### 1d · long_flat_gap_cd — 201 plateaus (top 5)

| fast/slow | Sharpe 15 bps | Sharpe 2 bps | return | max DD | windows > 0 | neighbours' median Sharpe | time in market |
|---|---|---|---|---|---|---|---|
| 6/36 | 1.21 | 1.24 | +5973.3% | -54% | 6/8 | 1.13 | 48% |
| 4/48 | 1.21 | 1.23 | +6023.8% | -61% | 6/8 | 1.14 | 49% |
| 10/28 | 1.20 | 1.23 | +5582.2% | -60% | 6/8 | 1.12 | 47% |
| 10/32 | 1.19 | 1.21 | +5340.0% | -60% | 6/8 | 1.11 | 48% |
| 6/40 | 1.18 | 1.21 | +5351.2% | -56% | 6/8 | 1.16 | 49% |

### 1d · long_flat_adx — 212 plateaus (top 5)

| fast/slow | Sharpe 15 bps | Sharpe 2 bps | return | max DD | windows > 0 | neighbours' median Sharpe | time in market |
|---|---|---|---|---|---|---|---|
| 10/88 | 1.32 | 1.35 | +5064.8% | -46% | 7/8 | 1.28 | 30% |
| 8/88 | 1.31 | 1.34 | +4966.5% | -48% | 7/8 | 1.29 | 30% |
| 8/92 | 1.30 | 1.33 | +4728.0% | -44% | 7/8 | 1.28 | 30% |
| 8/84 | 1.30 | 1.32 | +4912.0% | -43% | 7/8 | 1.27 | 30% |
| 22/68 | 1.29 | 1.32 | +4885.8% | -50% | 7/8 | 1.25 | 30% |

## Walk-forward detail (top 3 by OOS Sharpe)

#### 1d · long_flat_adx

| train → test | picked | in-sample Sharpe | OOS Sharpe | OOS return |
|---|---|---|---|---|
| W1 → W2 | 4/12 | 1.38 | 1.04 | +52.9% |
| W2 → W3 | 14/80 | 1.51 | 2.68 | +286.0% |
| W3 → W4 | 12/80 | 2.75 | 1.38 | +70.8% |
| W4 → W5 | 22/64 | 1.57 | 0.77 | +19.6% |
| W5 → W6 | 4/88 | 0.94 | 1.89 | +89.9% |
| W6 → W7 | 22/64 | 2.16 | 1.15 | +31.6% |
| W7 → W8 | 4/16 | 1.54 | -1.70 | -31.0% |

#### 8h · long_flat_adx

| train → test | picked | in-sample Sharpe | OOS Sharpe | OOS return |
|---|---|---|---|---|
| W1 → W2 | 8/24 | 0.81 | 1.16 | +57.7% |
| W2 → W3 | 24/96 | 2.07 | 2.50 | +235.1% |
| W3 → W4 | 24/44 | 2.62 | 1.29 | +60.5% |
| W4 → W5 | 6/32 | 2.19 | -0.25 | -10.3% |
| W5 → W6 | 4/84 | 0.32 | 2.72 | +122.1% |
| W6 → W7 | 20/60 | 2.89 | 0.20 | +2.3% |
| W7 → W8 | 20/88 | 0.51 | -0.26 | -6.8% |

#### 8h · long_flat

| train → test | picked | in-sample Sharpe | OOS Sharpe | OOS return |
|---|---|---|---|---|
| W1 → W2 | 4/12 | 2.05 | 1.28 | +75.8% |
| W2 → W3 | 24/96 | 1.68 | 2.56 | +346.3% |
| W3 → W4 | 22/64 | 2.83 | 1.15 | +67.0% |
| W4 → W5 | 14/76 | 1.45 | -0.59 | -24.1% |
| W5 → W6 | 4/32 | 0.10 | 1.81 | +87.2% |
| W6 → W7 | 18/60 | 2.39 | 0.95 | +33.5% |
| W7 → W8 | 8/48 | 1.69 | -0.04 | -4.5% |

## Caveats — each one is now tested, see `ROBUSTNESS_REPORT.md`

- Picking the best row across 50 sweeps is in-sample selection → a meta
  walk-forward also picks the variant *and* bar size from past data only, and a Deflated
  Sharpe Ratio discounts every book for the number of books tried.
- Textbook filter settings → a sensitivity grid moves every threshold and the vol target
  around its default and reports the worst case, without picking anything.
- "Do nothing" winning a walk-forward step → only combos with ≥ 3 trades in
  the training window can be picked.
- Optimistic fees → every headline number is at 15 bps (10 fee + 5 slippage), next-open fills.
- One asset / bullish sample → BTC history now starts 2017-08-17 (includes the 2018 bear),
  the same untuned books run on 9 coins and as a portfolio, and a paper trader
  records live decisions.

## Charts

- `wf_sharpe.png` — out-of-sample walk-forward Sharpe per variant and bar size, headline vs optimistic cost
- `heatmaps_trend.png` / `heatmaps_mean_reversion.png` — Sharpe over every grid, plateaus dotted
- `wf_equity.png` — out-of-sample equity per variant vs buy & hold (log scale)
- `candidates.png` — whole-grid ensembles (full size and vol-targeted) vs buy & hold, headline cost
