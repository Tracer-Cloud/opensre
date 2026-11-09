# Robustness report — every caveat, tested

Headline assumptions everywhere: **15 bps (10 fee + 5 slippage), next-open fills**. BTC/USDT history from
2017-08-17. Pass rule (fixed in advance): on W2–W8, Sharpe above *and* max drawdown
shallower than buy & hold sized the same way. Details per sweep: `HISTORY_REPORT.md`.

## 1. Execution test — fill at the next bar's open, pay 15 bps

Before, every decision filled at the close of the bar that produced it. A live bot can only act
*after* that close, so the headline now fills at the **next bar's open** and pays
10 bps fee + 5 bps slippage per unit of position change.
"1 bar late" fills a whole bar later (latency / missed run). Sharpe on W2–W8, ✓ = passes the
pass rule (Sharpe above *and* max drawdown shallower than the matching buy & hold).

| bars | book | sizing | close @15 | next open @15 | 1 bar late @15 | next open @2 | B&H Sharpe | max DD / B&H |
|---|---|---|---|---|---|---|---|---|
| 1d | always_in | full | 0.81 | **0.81** | 0.75 | 0.85 | 0.81 | -52% / -77% |
| 1d | always_in | vol-target | 0.87 ✓ | **0.87 ✓** | 0.75 | 0.92 | 0.78 | -45% / -65% |
| 1d | long_flat | full | 1.15 ✓ | **1.15 ✓** | 1.10 ✓ | 1.18 | 0.81 | -45% / -77% |
| 1d | long_flat | vol-target | 1.15 ✓ | **1.15 ✓** | 1.06 ✓ | 1.19 | 0.78 | -38% / -65% |
| 1d | long_flat_adx | full | 1.36 ✓ | **1.36 ✓** | 1.24 ✓ | 1.39 | 0.81 | -28% / -77% |
| 1d | long_flat_adx | vol-target | 1.44 ✓ | **1.44 ✓** | 1.29 ✓ | 1.49 | 0.78 | -27% / -65% |
| 1d | long_flat_gap_cd | full | 1.14 ✓ | **1.14 ✓** | 1.09 ✓ | 1.16 | 0.81 | -43% / -77% |
| 1d | long_flat_gap_cd | vol-target | 1.13 ✓ | **1.13 ✓** | 1.04 ✓ | 1.16 | 0.78 | -36% / -65% |
| 4h | always_in | full | 0.34 | **0.34** | 0.32 | 0.60 | 0.81 | -73% / -77% |
| 4h | always_in | vol-target | 0.69 | **0.69** | 0.63 | 0.99 | 0.80 | -43% / -66% |
| 4h | long_flat | full | 0.87 ✓ | **0.87 ✓** | 0.85 ✓ | 1.06 | 0.81 | -63% / -77% |
| 4h | long_flat | vol-target | 1.09 ✓ | **1.09 ✓** | 1.06 ✓ | 1.29 | 0.80 | -44% / -66% |
| 4h | long_flat_adx | full | 0.70 | **0.70** | 0.68 | 0.94 | 0.81 | -55% / -77% |
| 4h | long_flat_adx | vol-target | 0.87 ✓ | **0.87 ✓** | 0.84 ✓ | 1.13 | 0.80 | -42% / -66% |
| 4h | long_flat_gap_cd | full | 0.93 ✓ | **0.93 ✓** | 0.90 ✓ | 1.08 | 0.81 | -60% / -77% |
| 4h | long_flat_gap_cd | vol-target | 1.15 ✓ | **1.16 ✓** | 1.12 ✓ | 1.32 | 0.80 | -43% / -66% |
| 8h | always_in | full | 0.72 | **0.72** | 0.69 | 0.84 | 0.82 | -54% / -77% |
| 8h | always_in | vol-target | 1.03 ✓ | **1.03 ✓** | 0.95 ✓ | 1.18 | 0.79 | -40% / -66% |
| 8h | long_flat | full | 1.13 ✓ | **1.13 ✓** | 1.11 ✓ | 1.22 | 0.82 | -53% / -77% |
| 8h | long_flat | vol-target | 1.31 ✓ | **1.31 ✓** | 1.27 ✓ | 1.41 | 0.79 | -41% / -66% |
| 8h | long_flat_adx | full | 1.27 ✓ | **1.27 ✓** | 1.22 ✓ | 1.38 | 0.82 | -50% / -77% |
| 8h | long_flat_adx | vol-target | 1.32 ✓ | **1.32 ✓** | 1.24 ✓ | 1.45 | 0.79 | -39% / -66% |
| 8h | long_flat_gap_cd | full | 1.16 ✓ | **1.16 ✓** | 1.13 ✓ | 1.23 | 0.82 | -53% / -77% |
| 8h | long_flat_gap_cd | vol-target | 1.33 ✓ | **1.33 ✓** | 1.30 ✓ | 1.42 | 0.79 | -42% / -66% |

Books passing at the headline assumptions, and on which bar sizes:

- ensemble always_in (vol-target): passes on 8h, 1d — **robust**
- ensemble flat_adx (full): passes on 1d
- ensemble flat_adx (vol-target): passes on 8h, 1d — **robust**
- ensemble flat_adx_gap_cd (full): passes on 1d
- ensemble flat_adx_gap_cd (vol-target): passes on 8h, 1d — **robust**
- ensemble flat_er (vol-target): passes on 8h, 1d — **robust**
- ensemble gap_cooldown (vol-target): passes on 8h, 1d — **robust**
- ensemble long_flat (full): passes on 4h, 8h, 1d — **robust**
- ensemble long_flat (vol-target): passes on 4h, 8h, 1d — **robust**
- ensemble long_flat_adx (full): passes on 8h, 1d — **robust**
- ensemble long_flat_adx (vol-target): passes on 4h, 8h, 1d — **robust**
- ensemble long_flat_gap_cd (full): passes on 4h, 8h, 1d — **robust**
- ensemble long_flat_gap_cd (vol-target): passes on 4h, 8h, 1d — **robust**

## 2. Picking the best row — now picked out-of-sample too

The meta walk-forward chooses, on each window, the best book among **all
80 strategy books** (every variant ×
sizing × bar size) using only that window, and trades it on the next one. This is what you
would have earned by "picking the best row" without hindsight.

| train | test | picked | in-sample Sharpe | OOS Sharpe | OOS return |
|---|---|---|---|---|---|
| W1 | W2 | 4h ensemble long_flat (raw) | 1.53 | 1.18 | +68.4% |
| W2 | W3 | 1d ensemble flat_er (vol) | 2.37 | 2.20 | +144.7% |
| W3 | W4 | 1d ensemble long_flat_adx (vol) | 2.87 | 1.09 | +26.7% |
| W4 | W5 | 8h ensemble long_flat_adx (raw) | 1.48 | -0.38 | -13.3% |
| W5 | W6 | 1d ensemble long_flat_adx (raw) | 0.85 | 1.96 | +91.8% |
| W6 | W7 | 8h ensemble long_flat_adx (vol) | 2.48 | 0.19 | +2.1% |
| W7 | W8 | 1d ensemble long_flat_adx (raw) | 1.25 | -0.91 | -17.4% |

|  | Sharpe | return | max DD |
|---|---|---|---|
| meta walk-forward | 0.96 | +631.7% | -44% |
| buy & hold | 0.81 | +1095.3% | -77% |
| buy & hold vol-target | 0.78 | +607.5% | -65% |

## 3. Deflated Sharpe Ratio — how much of it could be luck?

DSR (Bailey & López de Prado, 2014) is the probability that a Sharpe ratio is real after
accounting for how many strategies were tried: **80** books, or (strictest) all
**9,040** parameter sets of the study. "vs B&H" tests the claim actually made — a *higher
Sharpe* than buy & hold — on the book's daily return minus buy & hold scaled to the same
volatility. ≥ 0.95 = significant. Daily returns, W2–W8.

How lucky the best of N could look is measured with the no-skill dispersion of Sharpe
estimates (√(1/T) = 0.018 per day). The raw spread across books (0.141 per day) is
not used: it comes from 15m books losing to costs deterministically, and with it no book could
pass at all (an earlier version of this report did that and showed 0.00 everywhere).

| bars | book | sizing | Sharpe | B&H Sharpe | DSR (80 books) | DSR (9,040 sets) | DSR vs B&H | DSR vs B&H (9,040 sets) |
|---|---|---|---|---|---|---|---|---|
| 1d | long_flat_adx | vol-target | 1.44 | 0.78 | 0.96 | 0.61 | **0.33** | 0.03 |
| 8h | long_flat_gap_cd | vol-target | 1.26 | 0.77 | 0.88 | 0.39 | **0.27** | 0.02 |
| 8h | long_flat | vol-target | 1.24 | 0.77 | 0.87 | 0.37 | **0.25** | 0.02 |
| 1d | long_flat_adx | full | 1.36 | 0.81 | 0.93 | 0.51 | **0.22** | 0.02 |
| 1d | long_flat | vol-target | 1.15 | 0.78 | 0.80 | 0.28 | **0.17** | 0.01 |
| 8h | long_flat_adx | vol-target | 1.25 | 0.77 | 0.88 | 0.38 | **0.16** | 0.01 |
| 1d | long_flat_gap_cd | vol-target | 1.13 | 0.78 | 0.78 | 0.26 | **0.14** | 0.01 |
| 1d | long_flat | full | 1.15 | 0.81 | 0.80 | 0.29 | **0.13** | 0.01 |
| 1d | long_flat_gap_cd | full | 1.14 | 0.81 | 0.79 | 0.27 | **0.11** | 0.00 |
| 8h | long_flat_adx | full | 1.23 | 0.81 | 0.86 | 0.35 | **0.11** | 0.00 |
| 4h | long_flat_gap_cd | vol-target | 1.10 | 0.78 | 0.76 | 0.23 | **0.10** | 0.00 |
| 8h | long_flat_gap_cd | full | 1.12 | 0.81 | 0.77 | 0.25 | **0.09** | 0.00 |

- Significantly positive on their own (80 books tried): **1/80**.
- Sharpe significantly above buy & hold: **0/80**.

## 4. Untuned thresholds — what if the textbook values were different?

Every setting is moved around its default and nothing is picked: vol target
30%, 40%, 50%, 60% (plus full size), gap
0.1, 0.25, 0.5 × ATR, cooldown
2, 4, 8 bars, ADX
20, 25, 30. Each setting is compared with buy & hold
sized the same way (same vol target), at the headline assumptions. "Sharpe margin" = book
minus buy & hold.

| book | bars | settings | pass rate | worst Sharpe margin | median Sharpe margin | worst max DD |
|---|---|---|---|---|---|---|
| long_flat | 4h | 5 | 100% | +0.06 | +0.24 | -63% |
| long_flat_gap_cd | 4h | 45 | 100% | +0.09 | +0.30 | -63% |
| long_flat_adx | 4h | 15 | 93% | -0.11 | +0.17 | -65% |
| long_flat | 8h | 5 | 100% | +0.32 | +0.48 | -53% |
| long_flat_gap_cd | 8h | 45 | 100% | +0.33 | +0.51 | -54% |
| long_flat_adx | 8h | 15 | 100% | +0.33 | +0.43 | -58% |
| long_flat | 1d | 5 | 100% | +0.34 | +0.36 | -45% |
| long_flat_gap_cd | 1d | 45 | 100% | +0.31 | +0.35 | -45% |
| long_flat_adx | 1d | 15 | 100% | +0.14 | +0.35 | -45% |

## 5. "Do nothing" winning a walk-forward step

The walk-forward may now only pick combos with at least 3 position changes
in the training window. Steps whose out-of-sample return is exactly 0 (nothing traded):
**2/350**.

## 6. One asset, one bullish sample → BTC since 2017 and 9 coins

BTC history now starts in Aug 2017 (includes the 2018 −84% bear market and the 2022 crash).
The **same untuned books** (no re-fitting per coin) run on ADAUSDT, BNBUSDT, BTCUSDT, ETHUSDT, LINKUSDT, LTCUSDT, TRXUSDT, XLMUSDT, XRPUSDT, each against its own buy &
hold on its own W2–W8, at the headline assumptions.

Coins passed, per book and bar size:

| book | sizing | bars | coins passed |
|---|---|---|---|
| long_flat | full | 4h | 6/9 |
| long_flat | vol-target | 4h | 7/9 |
| long_flat_gap_cd | full | 4h | 5/9 |
| long_flat_gap_cd | vol-target | 4h | 7/9 |
| long_flat_adx | full | 4h | 4/9 |
| long_flat_adx | vol-target | 4h | 5/9 |
| long_flat | full | 8h | 7/9 |
| long_flat | vol-target | 8h | 8/9 |
| long_flat_gap_cd | full | 8h | 6/9 |
| long_flat_gap_cd | vol-target | 8h | 8/9 |
| long_flat_adx | full | 8h | 4/9 |
| long_flat_adx | vol-target | 8h | 6/9 |
| long_flat | full | 1d | 4/9 |
| long_flat | vol-target | 1d | 4/9 |
| long_flat_gap_cd | full | 1d | 4/9 |
| long_flat_gap_cd | vol-target | 1d | 4/9 |
| long_flat_adx | full | 1d | 4/9 |
| long_flat_adx | vol-target | 1d | 4/9 |

Equal-weight portfolio (each coin enters after a 120-day warm-up; daily rebalanced; the
benchmark is the equal-weight buy & hold portfolio sized the same way):

| bars | book | sizing | Sharpe | CAGR | max DD | years > 0 | passes |
|---|---|---|---|---|---|---|---|
| 4h | buy & hold | full | 0.94 | +50.8% | -76% | 5/8 | bench |
| 4h | buy & hold | vol-target | 0.96 | +32.6% | -51% | 6/8 | bench |
| 4h | long_flat | full | 1.13 | +47.7% | -54% | 5/8 | **YES** |
| 4h | long_flat | vol-target | 1.30 | +29.8% | -30% | 5/8 | **YES** |
| 4h | long_flat_gap_cd | full | 1.17 | +48.5% | -50% | 5/8 | **YES** |
| 4h | long_flat_gap_cd | vol-target | 1.35 | +29.7% | -27% | 5/8 | **YES** |
| 4h | long_flat_adx | full | 1.22 | +34.8% | -32% | 5/8 | **YES** |
| 4h | long_flat_adx | vol-target | 1.35 | +20.0% | -18% | 6/8 | **YES** |
| 8h | buy & hold | full | 0.94 | +50.8% | -76% | 5/8 | bench |
| 8h | buy & hold | vol-target | 0.93 | +31.7% | -51% | 5/8 | bench |
| 8h | long_flat | full | 1.26 | +57.3% | -50% | 5/8 | **YES** |
| 8h | long_flat | vol-target | 1.38 | +33.4% | -29% | 5/8 | **YES** |
| 8h | long_flat_gap_cd | full | 1.25 | +54.8% | -50% | 5/8 | **YES** |
| 8h | long_flat_gap_cd | vol-target | 1.37 | +31.4% | -29% | 5/8 | **YES** |
| 8h | long_flat_adx | full | 1.05 | +30.3% | -46% | 5/8 | **YES** |
| 8h | long_flat_adx | vol-target | 1.11 | +16.7% | -26% | 6/8 | **YES** |
| 1d | buy & hold | full | 0.94 | +50.8% | -76% | 5/8 | bench |
| 1d | buy & hold | vol-target | 0.97 | +33.2% | -52% | 6/8 | bench |
| 1d | long_flat | full | 1.07 | +48.1% | -55% | 5/8 | **YES** |
| 1d | long_flat | vol-target | 1.13 | +25.8% | -32% | 6/8 | **YES** |
| 1d | long_flat_gap_cd | full | 1.04 | +44.8% | -56% | 5/8 | **YES** |
| 1d | long_flat_gap_cd | vol-target | 1.10 | +23.9% | -30% | 6/8 | **YES** |
| 1d | long_flat_adx | full | 1.03 | +32.7% | -40% | 6/8 | **YES** |
| 1d | long_flat_adx | vol-target | 1.11 | +16.7% | -22% | 6/8 | **YES** |

Survivorship bias: coins delisted since 2018 cannot be downloaded, so the basket is made of
survivors. That flatters buy & hold more than trend following (which exits collapsing coins),
but it still means the coin list was chosen with hindsight.

## 7. Paper trading — the only test hindsight cannot touch

Pre-registered (chosen before the checks above were run):

- BTCUSDT 4h `long_flat_gap_cd` (vol-target)

`run_paper.py` appends every decision to an append-only ledger at the moment it is made and
prices it like the study (15 bps (10 fee + 5 slippage), next-open fills). Run it 24/7 on your machine:

```bash
cd quant_desk && nohup .venv/bin/python run_paper.py --loop >> output/paper/paper.log 2>&1 &
# or cron, every 4 hours:  2 */4 * * *  cd /path/to/quant_desk && .venv/bin/python run_paper.py
```

Current status (`output/paper/STATUS.md`):

Priced at 15 bps (10 fee + 5 slippage), next-open fills. Ledger is append-only (ArcticDB library `paper`).

| book | started | decisions | last bar | exposure now | paper P&L | buy & hold | max DD |
|---|---|---|---|---|---|---|---|
| BTCUSDT_4h_long_flat_gap_cd_vol | 2026-11-09 00:00 | 1 (0 late) | 2026-11-09 00:00 | 0.00 | +0.00% | +0.00% | 0.0% |


## What is still not solved

- Long/flat was added after seeing that shorting BTC lost money: hindsight that no backtest can
  remove. Paper trading is the fix, and it needs months.
- Binance only: other exchanges are blocked by this environment's network policy.
- Survivorship in the coin list (above).
