"""Static settings for the quant desk: market, storage, fees and the sweep grid."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
OUTPUT_DIR = ROOT / "output"
ARCTIC_URI = f"lmdb://{DATA_DIR / 'arctic'}"
ARCTIC_LIBRARY = "market"

# Binance's public market-data mirror needs no API key. BTC/USDT stands in for BTC/USD.
BINANCE_KLINES_URL = "https://data-api.binance.vision/api/v3/klines"
BINANCE_MAX_LIMIT = 1000
SYMBOL = "BTCUSDT"
LOOKBACK_DAYS = 30
RAW_INTERVAL = "1m"
BAR_RULE = "15min"
BARS_PER_DAY = 96  # 24h / 15m

SYMBOL_RAW = f"{SYMBOL}_1m"
SYMBOL_BARS = f"{SYMBOL}_15m"
SYMBOL_SWEEP = f"{SYMBOL}_15m_ema_sweep"

# Crypto trades 24/7, so a year is 365 days of 15-minute bars.
ANNUALIZATION = (365 * BARS_PER_DAY) ** 0.5

FEE_BPS = 2.0  # charged per unit of position change: entry 2 bps, long<->short flip 4 bps

# Sweep grid: fast 4..24 step 2, slow 12..96 step 4, keep slow >= 1.5 * fast -> 212 combos.
FAST_SPANS = tuple(range(4, 25, 2))
SLOW_SPANS = tuple(range(12, 97, 4))
MIN_SLOW_OVER_FAST = 1.5
EXPECTED_COMBOS = 212

N_WINDOWS = 4

# Indicator defaults (in 15m bars).
BOLLINGER_WINDOW = 20
BOLLINGER_K = 2.0
RSI_WINDOW = 14
VOLUME_PROFILE_BINS = 40

# Plateau test: neighbours (+-1 grid step) must keep at least this share of the combo's Sharpe,
# and the combo must make money in at least this many of the N_WINDOWS windows.
PLATEAU_NEIGHBOUR_RATIO = 0.5
PLATEAU_MIN_POSITIVE_WINDOWS = 3
TOP_N_LABELLED = 15

# --- Multi-year study (run_history.py) --------------------------------------------------------
HISTORY_START = "2022-01-01"  # 2022 bear, 2023 recovery, 2024-25 bull, 2026 chop
HISTORY_INTERVAL = "15m"  # fetched directly as 15m klines (same OHLCV as resampled 1m)
SYMBOL_HISTORY = f"{SYMBOL}_15m_history"
TIMEFRAMES = {"15m": 15, "1h": 60, "4h": 240, "8h": 480, "1d": 1440}  # label -> minutes
HISTORY_WINDOWS = 8
HISTORY_MIN_POSITIVE_SHARE = 0.75  # plateau must be profitable in >= 6 of 8 windows

# Regime filters: textbook defaults, deliberately NOT tuned (tuning them is more overfitting).
ER_WINDOW = 20  # Kaufman efficiency ratio: |net move| / path length over N bars
ER_TREND = 0.30  # >= trend regime, below = chop
ADX_WINDOW = 14
ADX_TREND = 25.0
ATR_WINDOW = 14
MIN_GAP_ATR = 0.25  # enter only when |EMA fast - EMA slow| >= 0.25 x ATR
COOLDOWN_BARS = 4  # bars to wait after any position change before entering again

# Mean reversion (Bollinger fade): window x width grid.
MR_WINDOWS = (10, 14, 20, 26, 32, 40, 50, 60)
MR_WIDTHS = (1.5, 1.75, 2.0, 2.25, 2.5, 2.75, 3.0)


def annualization(bar_minutes: int) -> float:
    """sqrt(bars per year) for a 24/7 market."""
    return (365 * 24 * 60 / bar_minutes) ** 0.5


# --- Robust portfolio construction (no parameter picking) -------------------------------------
VOL_TARGET = 0.40  # annualised; BTC's own realised vol is usually 40-80%
VOL_WINDOW_DAYS = 30
VOL_MAX_WEIGHT = 1.0  # never lever up, only scale down in high volatility
STUDY_WORKERS = 4  # processes for run_history.py
