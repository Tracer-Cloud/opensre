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
| 1d | always_in | full | 0.81 ✓ | **0.81 ✓** | 0.75 | 0.85 | 0.81 | -52% / -77% |
| 1d | always_in | vol-target | 0.87 ✓ | **0.87 ✓** | 0.75 | 0.93 | 0.78 | -45% / -65% |
| 1d | long_flat | full | 1.15 ✓ | **1.15 ✓** | 1.10 ✓ | 1.18 | 0.81 | -45% / -77% |
| 1d | long_flat | vol-target | 1.15 ✓ | **1.15 ✓** | 1.06 ✓ | 1.19 | 0.78 | -38% / -65% |
| 1d | long_flat_adx | full | 1.36 ✓ | **1.36 ✓** | 1.24 ✓ | 1.39 | 0.81 | -28% / -77% |
| 1d | long_flat_adx | vol-target | 1.44 ✓ | **1.44 ✓** | 1.29 ✓ | 1.49 | 0.78 | -27% / -65% |
| 1d | long_flat_gap_cd | full | 1.14 ✓ | **1.14 ✓** | 1.08 ✓ | 1.16 | 0.81 | -43% / -77% |
| 1d | long_flat_gap_cd | vol-target | 1.13 ✓ | **1.13 ✓** | 1.04 ✓ | 1.16 | 0.78 | -36% / -65% |
| 4h | always_in | full | 0.34 | **0.35** | 0.32 | 0.61 | 0.81 | -73% / -77% |
| 4h | always_in | vol-target | 0.70 | **0.70** | 0.63 | 0.99 | 0.80 | -43% / -66% |
| 4h | long_flat | full | 0.87 ✓ | **0.87 ✓** | 0.85 ✓ | 1.06 | 0.81 | -63% / -77% |
| 4h | long_flat | vol-target | 1.09 ✓ | **1.09 ✓** | 1.06 ✓ | 1.29 | 0.80 | -44% / -66% |
| 4h | long_flat_adx | full | 0.70 | **0.70** | 0.68 | 0.94 | 0.81 | -55% / -77% |
| 4h | long_flat_adx | vol-target | 0.87 ✓ | **0.87 ✓** | 0.84 ✓ | 1.13 | 0.80 | -42% / -66% |
| 4h | long_flat_gap_cd | full | 0.93 ✓ | **0.93 ✓** | 0.90 ✓ | 1.08 | 0.81 | -60% / -77% |
| 4h | long_flat_gap_cd | vol-target | 1.15 ✓ | **1.16 ✓** | 1.12 ✓ | 1.32 | 0.80 | -43% / -66% |
| 8h | always_in | full | 0.72 | **0.72** | 0.70 | 0.85 | 0.81 | -54% / -77% |
| 8h | always_in | vol-target | 1.04 ✓ | **1.04 ✓** | 0.95 ✓ | 1.19 | 0.78 | -40% / -66% |
| 8h | long_flat | full | 1.13 ✓ | **1.13 ✓** | 1.11 ✓ | 1.22 | 0.81 | -53% / -77% |
| 8h | long_flat | vol-target | 1.31 ✓ | **1.31 ✓** | 1.27 ✓ | 1.41 | 0.78 | -41% / -66% |
| 8h | long_flat_adx | full | 1.27 ✓ | **1.27 ✓** | 1.22 ✓ | 1.38 | 0.81 | -50% / -77% |
| 8h | long_flat_adx | vol-target | 1.32 ✓ | **1.32 ✓** | 1.24 ✓ | 1.45 | 0.78 | -39% / -66% |
| 8h | long_flat_gap_cd | full | 1.16 ✓ | **1.16 ✓** | 1.13 ✓ | 1.23 | 0.81 | -53% / -77% |
| 8h | long_flat_gap_cd | vol-target | 1.33 ✓ | **1.33 ✓** | 1.30 ✓ | 1.42 | 0.78 | -42% / -66% |

Books passing at the headline assumptions, and on which bar sizes:

- ensemble always_in (full): passes on 1d
- ensemble always_in (vol-target): passes on 8h, 1d — **robust**
- ensemble flat_adx (full): passes on 1d
- ensemble flat_adx (vol-target): passes on 8h, 1d — **robust**
- ensemble flat_adx_gap_cd (full): passes on 1d
- ensemble flat_adx_gap_cd (vol-target): passes on 8h, 1d — **robust**
- ensemble flat_er (full): passes on 8h
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
| buy & hold | 0.81 | +1079.6% | -77% |
| buy & hold vol-target | 0.78 | +598.2% | -65% |

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
| 8h | long_flat | vol-target | 1.24 | 0.77 | 0.87 | 0.37 | **0.26** | 0.02 |
| 1d | long_flat_adx | full | 1.36 | 0.81 | 0.93 | 0.51 | **0.22** | 0.02 |
| 1d | long_flat | vol-target | 1.15 | 0.78 | 0.80 | 0.28 | **0.17** | 0.01 |
| 8h | long_flat_adx | vol-target | 1.25 | 0.77 | 0.88 | 0.38 | **0.16** | 0.01 |
| 1d | long_flat_gap_cd | vol-target | 1.13 | 0.78 | 0.78 | 0.26 | **0.15** | 0.01 |
| 1d | long_flat | full | 1.15 | 0.81 | 0.80 | 0.29 | **0.13** | 0.01 |
| 1d | long_flat_gap_cd | full | 1.14 | 0.81 | 0.79 | 0.27 | **0.12** | 0.00 |
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
| long_flat_gap_cd | 4h | 45 | 100% | +0.10 | +0.30 | -63% |
| long_flat_adx | 4h | 15 | 93% | -0.11 | +0.17 | -65% |
| long_flat | 8h | 5 | 100% | +0.32 | +0.48 | -53% |
| long_flat_gap_cd | 8h | 45 | 100% | +0.33 | +0.51 | -54% |
| long_flat_adx | 8h | 15 | 100% | +0.34 | +0.43 | -58% |
| long_flat | 1d | 5 | 100% | +0.35 | +0.37 | -45% |
| long_flat_gap_cd | 1d | 45 | 100% | +0.31 | +0.35 | -45% |
| long_flat_adx | 1d | 15 | 100% | +0.15 | +0.35 | -45% |

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
| long_flat | full | 4h | 5/9 |
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
| 4h | buy & hold | full | 0.94 | +50.9% | -76% | 5/8 | bench |
| 4h | buy & hold | vol-target | 0.96 | +32.7% | -51% | 6/8 | bench |
| 4h | long_flat | full | 1.13 | +47.7% | -54% | 5/8 | **YES** |
| 4h | long_flat | vol-target | 1.30 | +29.8% | -30% | 5/8 | **YES** |
| 4h | long_flat_gap_cd | full | 1.17 | +48.5% | -50% | 5/8 | **YES** |
| 4h | long_flat_gap_cd | vol-target | 1.35 | +29.7% | -27% | 5/8 | **YES** |
| 4h | long_flat_adx | full | 1.22 | +34.8% | -32% | 5/8 | **YES** |
| 4h | long_flat_adx | vol-target | 1.35 | +20.0% | -18% | 6/8 | **YES** |
| 8h | buy & hold | full | 0.94 | +50.9% | -76% | 5/8 | bench |
| 8h | buy & hold | vol-target | 0.93 | +31.7% | -51% | 5/8 | bench |
| 8h | long_flat | full | 1.26 | +57.3% | -50% | 5/8 | **YES** |
| 8h | long_flat | vol-target | 1.37 | +33.4% | -29% | 5/8 | **YES** |
| 8h | long_flat_gap_cd | full | 1.25 | +54.7% | -50% | 5/8 | **YES** |
| 8h | long_flat_gap_cd | vol-target | 1.37 | +31.4% | -29% | 5/8 | **YES** |
| 8h | long_flat_adx | full | 1.05 | +30.3% | -46% | 5/8 | **YES** |
| 8h | long_flat_adx | vol-target | 1.11 | +16.7% | -26% | 6/8 | **YES** |
| 1d | buy & hold | full | 0.94 | +50.9% | -76% | 5/8 | bench |
| 1d | buy & hold | vol-target | 0.97 | +33.3% | -52% | 6/8 | bench |
| 1d | long_flat | full | 1.07 | +48.1% | -55% | 5/8 | **YES** |
| 1d | long_flat | vol-target | 1.13 | +25.8% | -32% | 6/8 | **YES** |
| 1d | long_flat_gap_cd | full | 1.04 | +44.8% | -56% | 5/8 | **YES** |
| 1d | long_flat_gap_cd | vol-target | 1.10 | +23.9% | -30% | 6/8 | **YES** |
| 1d | long_flat_adx | full | 1.03 | +32.6% | -40% | 6/8 | **YES** |
| 1d | long_flat_adx | vol-target | 1.11 | +16.7% | -22% | 6/8 | **YES** |

This 9-coin basket is made of today's survivors, so it carries survivorship bias; section 9
rebuilds the test on a point-in-time universe that includes the coins that died.

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
| BTCUSDT_4h_long_flat_gap_cd_vol | 2026-11-09 00:00 | 2 (0 late) | 2026-11-09 04:00 | 0.00 | +0.00% | +0.00% | 0.0% |


## 8. Long-only was chosen in hindsight — tested on data the decision never saw

The long/flat variants were added after looking at BTC on Binance from
2022-01-01. Each long/short variant is paired with its long/flat twin, and the
**short side's contribution** (long/short minus long/flat, with its own costs) is scored on data
that played no part in that decision: BTC on Bitstamp 2013–2017, BTC on Binance 2017–2021, eight
other coins, and the survivorship-free universe (which includes coins that collapsed — exactly
where shorting should shine).

| data | comparisons | long/flat Sharpe ≥ long/short | short side loses money | median short-side Sharpe | median short-side return / yr |
|---|---|---|---|---|---|
| BTC Bitstamp 2013–2017 | 18 | 100% | 61% | -0.04 | -7.9% |
| BTC Binance 2017–2021 | 18 | 100% | 89% | -0.08 | -10.0% |
| BTC Binance 2022– (decision data) ⚠ decision data | 18 | 100% | 100% | -0.12 | -7.6% |
| 8 other coins | 144 | 97% | 90% | -0.22 | -13.7% |

On never-seen data, long/flat had a Sharpe at least as high in **98%** of
180 comparisons and the short side lost money in **87%**.

BTC hold-outs in detail (vol-targeted):

| data | bars | pair | long/short | long/flat | short side Sharpe | short side / yr |
|---|---|---|---|---|---|---|
| BTC Bitstamp 2013–2017 | 4h | always_in → long_flat | 2.03 | 2.33 | +0.36 | +6.3% |
| BTC Bitstamp 2013–2017 | 4h | gap_cooldown → long_flat_gap_cd | 2.01 | 2.35 | +0.31 | +4.9% |
| BTC Bitstamp 2013–2017 | 4h | flat_adx → long_flat_adx | 1.34 | 1.87 | -0.10 | -5.7% |
| BTC Bitstamp 2013–2017 | 8h | always_in → long_flat | 1.80 | 2.08 | +0.23 | +2.5% |
| BTC Bitstamp 2013–2017 | 8h | gap_cooldown → long_flat_gap_cd | 1.81 | 2.11 | +0.23 | +2.6% |
| BTC Bitstamp 2013–2017 | 8h | flat_adx → long_flat_adx | 1.48 | 2.06 | -0.17 | -7.3% |
| BTC Bitstamp 2013–2017 | 1d | always_in → long_flat | 1.76 | 1.87 | +0.29 | +4.2% |
| BTC Bitstamp 2013–2017 | 1d | gap_cooldown → long_flat_gap_cd | 1.62 | 1.84 | +0.14 | +0.4% |
| BTC Bitstamp 2013–2017 | 1d | flat_adx → long_flat_adx | 1.79 | 1.89 | +0.31 | +4.5% |
| BTC Binance 2017–2021 | 4h | always_in → long_flat | 1.00 | 1.31 | +0.08 | -1.9% |
| BTC Binance 2017–2021 | 4h | gap_cooldown → long_flat_gap_cd | 0.99 | 1.33 | +0.04 | -2.9% |
| BTC Binance 2017–2021 | 4h | flat_adx → long_flat_adx | 0.69 | 1.19 | -0.17 | -7.6% |
| BTC Binance 2017–2021 | 8h | always_in → long_flat | 1.19 | 1.40 | +0.22 | +2.3% |
| BTC Binance 2017–2021 | 8h | gap_cooldown → long_flat_gap_cd | 1.23 | 1.46 | +0.24 | +2.9% |
| BTC Binance 2017–2021 | 8h | flat_adx → long_flat_adx | 0.96 | 1.54 | -0.17 | -7.4% |
| BTC Binance 2017–2021 | 1d | always_in → long_flat | 1.10 | 1.27 | +0.14 | -0.1% |
| BTC Binance 2017–2021 | 1d | gap_cooldown → long_flat_gap_cd | 0.97 | 1.31 | -0.11 | -6.0% |
| BTC Binance 2017–2021 | 1d | flat_adx → long_flat_adx | 1.25 | 1.62 | +0.09 | -0.8% |

Survivorship-free universe (vol-targeted), long/short vs long/flat:

| bars | pair | Sharpe long/short | Sharpe long/flat | max DD long/short | max DD long/flat |
|---|---|---|---|---|---|
| 4h | always_in → long_flat | 0.76 | 1.05 | -26% | -26% |
| 8h | always_in → long_flat | 0.95 | 1.10 | -29% | -31% |
| 1d | always_in → long_flat | 0.40 | 0.73 | -46% | -41% |
| 4h | gap_cooldown → long_flat_gap_cd | 0.77 | 1.10 | -24% | -25% |
| 8h | gap_cooldown → long_flat_gap_cd | 0.88 | 1.10 | -32% | -30% |
| 1d | gap_cooldown → long_flat_gap_cd | 0.31 | 0.72 | -45% | -39% |
| 4h | flat_adx → long_flat_adx | 0.62 | 1.09 | -19% | -17% |
| 8h | flat_adx → long_flat_adx | 0.51 | 0.96 | -24% | -22% |
| 1d | flat_adx → long_flat_adx | 0.59 | 0.88 | -32% | -18% |

## 9. Survivorship bias — a point-in-time universe with the dead coins put back

Binance still serves the history of delisted pairs: **703** risk-asset USDT pairs were
downloaded, **199** of them delisted. Every month the universe is the top
**10** by trailing 30-day USDT volume, chosen only
from data before that month (≥ 120 days of history, still trading).
**86** instruments were members at some point; **13** of them were later
delisted or stopped trading. A ticker re-used after a halt (old LUNA → new LUNA) is split, never
stitched. Each member gets 1/10 of capital; a coin whose data stops exits at
its last price with costs. Benchmark: equal-weight buy & hold of the same monthly members.
"Survivors only" rebuilds the universe from coins still listed today — the gap between the two
is the bias the earlier 9-coin test carried.

| bars | book | sizing | Sharpe | CAGR | max DD | passes | survivors-only Sharpe | survivors-only CAGR |
|---|---|---|---|---|---|---|---|---|
| 4h | buy & hold | full | 0.42 | +0.1% | -94% | bench | 0.44 | +1.8% |
| 4h | buy & hold | vol-target | 0.57 | +14.7% | -58% | bench | 0.60 | +15.8% |
| 4h | long_flat | full | 0.81 | +28.6% | -57% | **YES** | 0.86 | +31.2% |
| 4h | long_flat | vol-target | 1.05 | +20.5% | -26% | **YES** | 1.10 | +21.4% |
| 4h | long_flat_gap_cd | full | 0.85 | +29.5% | -54% | **YES** | 0.90 | +32.2% |
| 4h | long_flat_gap_cd | vol-target | 1.10 | +20.7% | -25% | **YES** | 1.15 | +21.6% |
| 4h | long_flat_adx | full | 0.90 | +22.4% | -40% | **YES** | 0.96 | +24.1% |
| 4h | long_flat_adx | vol-target | 1.09 | +13.9% | -17% | **YES** | 1.12 | +14.4% |
| 8h | buy & hold | full | 0.36 | -4.7% | -95% | bench | 0.38 | -2.9% |
| 8h | buy & hold | vol-target | 0.52 | +12.8% | -61% | bench | 0.55 | +14.0% |
| 8h | long_flat | full | 0.89 | +33.8% | -65% | **YES** | 0.96 | +37.9% |
| 8h | long_flat | vol-target | 1.10 | +22.7% | -31% | **YES** | 1.16 | +24.1% |
| 8h | long_flat_gap_cd | full | 0.90 | +33.0% | -63% | **YES** | 0.96 | +36.4% |
| 8h | long_flat_gap_cd | vol-target | 1.10 | +21.6% | -30% | **YES** | 1.15 | +22.7% |
| 8h | long_flat_adx | full | 0.84 | +22.2% | -50% | **YES** | 0.96 | +26.0% |
| 8h | long_flat_adx | vol-target | 0.96 | +12.9% | -22% | **YES** | 1.06 | +14.3% |
| 1d | buy & hold | full | 0.44 | +3.1% | -92% | bench | 0.47 | +5.1% |
| 1d | buy & hold | vol-target | 0.59 | +15.6% | -58% | bench | 0.62 | +16.7% |
| 1d | long_flat | full | 0.59 | +18.4% | -74% | **YES** | 0.66 | +22.3% |
| 1d | long_flat | vol-target | 0.73 | +14.2% | -41% | **YES** | 0.77 | +15.0% |
| 1d | long_flat_gap_cd | full | 0.58 | +17.5% | -71% | **YES** | 0.64 | +20.8% |
| 1d | long_flat_gap_cd | vol-target | 0.72 | +13.3% | -39% | **YES** | 0.74 | +13.8% |
| 1d | long_flat_adx | full | 0.71 | +20.0% | -46% | **YES** | 0.72 | +20.8% |
| 1d | long_flat_adx | vol-target | 0.88 | +11.8% | -18% | **YES** | 0.89 | +11.9% |

Members that later died or were delisted (8h bars), while they were members:

| instrument | status | months in universe | last bar | buy & hold | long_flat_gap_cd |
|---|---|---|---|---|---|
| LUNAUSDT | ended | 5 | 2022-05-13 | -100.0% | -8.4% |
| EOSUSDT | ended | 27 | 2025-05-26 | -72.6% | +5.6% |
| MATICUSDT | ended | 21 | 2024-09-10 | -69.6% | -29.2% |
| BTTUSDT | ended | 3 | 2022-01-17 | -52.6% | -42.6% |
| FTMUSDT | ended | 5 | 2025-01-13 | -42.6% | +30.0% |
| BCCUSDT | ended | 8 | 2018-11-20 | -31.7% | -18.3% |
| LRCUSDT | ended | 1 | 2026-04-01 | -27.6% | -23.7% |
| RNDRUSDT | ended | 1 | 2024-07-22 | -18.4% | -13.7% |
| TONUSDT | ended | 1 | 2026-06-30 | -17.1% | -13.9% |
| HIFIUSDT | ended | 1 | 2025-09-17 | -15.3% | -3.6% |
| LINAUSDT | ended | 1 | 2025-03-28 | -6.2% | -6.7% |
| LOOMUSDT | ended | 1 | 2024-08-26 | +2.9% | -5.8% |

## 10. Binance-only — the same BTC books on Bitstamp and Coinbase

Bitstamp (BTC/USD since 2013) and Coinbase (BTC-USD since 2015) were added. Same books, same
costs and execution, each venue against its own buy & hold. Sharpe from 2018 (✓ = passes):

| bars | book | sizing | binance | bitstamp | coinbase |
|---|---|---|---|---|---|
| 4h | buy & hold | full | 0.62 | 0.61 | 0.62 |
| 4h | long_flat | full | 0.78 ✓ | 0.75 ✓ | 0.76 ✓ |
| 4h | long_flat_gap_cd | full | 0.83 ✓ | 0.80 ✓ | 0.81 ✓ |
| 4h | long_flat_adx | full | 0.55 | 0.78 ✓ | 0.71 ✓ |
| 4h | buy & hold | vol-target | 0.65 | 0.64 | 0.64 |
| 4h | long_flat | vol-target | 0.98 ✓ | 0.96 ✓ | 0.96 ✓ |
| 4h | long_flat_gap_cd | vol-target | 1.03 ✓ | 1.02 ✓ | 1.02 ✓ |
| 4h | long_flat_adx | vol-target | 0.75 ✓ | 0.98 ✓ | 0.91 ✓ |
| 8h | buy & hold | full | 0.63 | 0.62 | 0.62 |
| 8h | long_flat | full | 0.93 ✓ | 0.92 ✓ | 0.93 ✓ |
| 8h | long_flat_gap_cd | full | 0.94 ✓ | 0.94 ✓ | 0.94 ✓ |
| 8h | long_flat_adx | full | 0.99 ✓ | 0.99 ✓ | 1.03 ✓ |
| 8h | buy & hold | vol-target | 0.65 | 0.64 | 0.64 |
| 8h | long_flat | vol-target | 1.13 ✓ | 1.13 ✓ | 1.14 ✓ |
| 8h | long_flat_gap_cd | vol-target | 1.15 ✓ | 1.16 ✓ | 1.16 ✓ |
| 8h | long_flat_adx | vol-target | 1.12 ✓ | 1.14 ✓ | 1.17 ✓ |
| 1d | buy & hold | full | 0.64 | 0.64 | 0.63 |
| 1d | long_flat | full | 0.92 ✓ | 0.92 ✓ | 0.92 ✓ |
| 1d | long_flat_gap_cd | full | 0.92 ✓ | 0.91 ✓ | 0.92 ✓ |
| 1d | long_flat_adx | full | 1.20 ✓ | 1.21 ✓ | 1.14 ✓ |
| 1d | buy & hold | vol-target | 0.65 | 0.64 | 0.64 |
| 1d | long_flat | vol-target | 0.97 ✓ | 0.96 ✓ | 0.96 ✓ |
| 1d | long_flat_gap_cd | vol-target | 0.97 ✓ | 0.95 ✓ | 0.95 ✓ |
| 1d | long_flat_adx | vol-target | 1.30 ✓ | 1.27 ✓ | 1.21 ✓ |

Correlation of the venues' daily returns (long_flat_gap_cd, 4h, vol-targeted):

|  | binance | bitstamp | coinbase |
|---|---|---|---|
| binance | 1.0 | 0.997 | 0.997 |
| bitstamp | 0.997 | 1.0 | 1.0 |
| coinbase | 0.997 | 1.0 | 1.0 |

**Bitstamp 2013-01 → 2017-08** — BTC history that no decision in this project ever looked at
(includes the 2014 crash). Passes: **18/18**.

| bars | book | sizing | Sharpe | CAGR | max DD | passes |
|---|---|---|---|---|---|---|
| 4h | buy & hold | full | 1.38 | +121.9% | -85% | bench |
| 4h | long_flat | full | 1.98 | +155.1% | -45% | **YES** |
| 4h | long_flat_gap_cd | full | 2.00 | +154.6% | -43% | **YES** |
| 4h | long_flat_adx | full | 1.62 | +91.4% | -28% | **YES** |
| 4h | buy & hold | vol-target | 1.41 | +66.2% | -62% | bench |
| 4h | long_flat | vol-target | 2.33 | +93.7% | -21% | **YES** |
| 4h | long_flat_gap_cd | vol-target | 2.35 | +92.7% | -21% | **YES** |
| 4h | long_flat_adx | vol-target | 1.87 | +58.1% | -18% | **YES** |
| 8h | buy & hold | full | 1.38 | +122.0% | -85% | bench |
| 8h | long_flat | full | 1.67 | +123.3% | -60% | **YES** |
| 8h | long_flat_gap_cd | full | 1.71 | +125.7% | -56% | **YES** |
| 8h | long_flat_adx | full | 1.61 | +102.9% | -53% | **YES** |
| 8h | buy & hold | vol-target | 1.35 | +63.7% | -63% | bench |
| 8h | long_flat | vol-target | 2.08 | +83.8% | -26% | **YES** |
| 8h | long_flat_gap_cd | vol-target | 2.11 | +83.7% | -24% | **YES** |
| 8h | long_flat_adx | vol-target | 2.06 | +70.8% | -24% | **YES** |
| 1d | buy & hold | full | 1.37 | +120.2% | -85% | bench |
| 1d | long_flat | full | 1.69 | +140.1% | -53% | **YES** |
| 1d | long_flat_gap_cd | full | 1.67 | +135.9% | -53% | **YES** |
| 1d | long_flat_adx | full | 1.70 | +124.5% | -53% | **YES** |
| 1d | buy & hold | vol-target | 1.32 | +63.0% | -62% | bench |
| 1d | long_flat | vol-target | 1.87 | +79.4% | -25% | **YES** |
| 1d | long_flat_gap_cd | vol-target | 1.84 | +76.6% | -24% | **YES** |
| 1d | long_flat_adx | vol-target | 1.89 | +67.4% | -28% | **YES** |

## What is still open

- **Long-only hindsight**: on never-seen data long/flat matched or beat long/short in
  98% of comparisons. That tests the decision; it cannot make it un-seen. Paper
  trading remains the only clean test.
- **Survivorship**: solved for every coin Binance ever listed (12/12
  long/flat books pass on the point-in-time universe). Coins never listed on Binance are not
  covered.
- **Venues**: BTC is now checked on Bitstamp and Coinbase (18/18
  books pass on Bitstamp 2013–2017). Altcoins remain Binance-only.
- Still one asset class (crypto), and all of it is history. Run `run_paper.py --loop`.
