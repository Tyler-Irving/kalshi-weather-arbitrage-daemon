"""
Configuration constants, environment loading, and city/series data.

All trading parameters, risk limits, and static data live here.
"""
import os
from pathlib import Path

# ── Environment loading ──────────────────────────────────────────────────

BASE_DIR = Path(__file__).parent.parent


def _load_env():
    """Load .env file into os.environ (does not override existing vars)."""
    env_path = BASE_DIR / ".env"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())


_load_env()

# ── Paper trading mode ───────────────────────────────────────────────────

PAPER_TRADING = os.getenv("PAPER_TRADING", "true").lower() == "true"
PAPER_TRADING_NOTIFICATIONS = False

# ── File paths ───────────────────────────────────────────────────────────

LOG_PATH = BASE_DIR / "kalshi_unified_log.txt"
STATE_PATH = BASE_DIR / "kalshi_unified_state.json"
PNL_PATH = BASE_DIR / "kalshi_pnl.json"
PAPER_TRADES_PATH = BASE_DIR / "paper_trades.jsonl"
BACKTEST_PATH = BASE_DIR / "kalshi_backtest_log.jsonl"
SETTLEMENT_LOG_PATH = BASE_DIR / "kalshi_settlement_log.jsonl"

# ── Kalshi API ───────────────────────────────────────────────────────────

KALSHI_BASE = "https://api.elections.kalshi.com"
KALSHI_API_KEY_ID = os.getenv("KALSHI_API_KEY_ID", "")
KALSHI_PRIVATE_KEY_PATH = os.getenv("KALSHI_PRIVATE_KEY_PATH", "")

# ── Trading parameters ───────────────────────────────────────────────────

MAX_CONTRACTS = 8
MAX_COST_PER_TRADE = 500          # cents ($5)
MAX_OPEN_POSITIONS = 20
MAX_DAILY_TRADES = 40
MIN_VOLUME = 10
POLL_INTERVAL = 900               # 15 min
FORECAST_STD_DEV = 1.1            # baseline forecast RMSE in °F
MIN_PROVIDER_COUNT = 1
MAX_LOG_LINES = 200
MAX_EDGE_CENTS = 60               # edge sanity cap
MAX_SPREAD = 30                   # max yes_ask - yes_bid before skipping
NOAA_STALE_HOURS = 6
NOAA_STALE_PENALTY = 0.5

# Paper vs live: paper mode loosens filters for more opportunity volume
if PAPER_TRADING:
    MIN_EDGE_CENTS = 10
    MIN_YES_PRICE = 5
    MIN_NO_PRICE = 5
    MIN_CONFIDENCE_SCORE = 0.5
    MODEL_WEIGHT = 0.3
    MAX_DISAGREEMENT_CENTS = 40
    MAX_FAIR_MARKET_RATIO = 3.5
else:
    MIN_EDGE_CENTS = 15
    MIN_CONFIDENCE_SCORE = 0.6
    MODEL_WEIGHT = 0.3
    MIN_YES_PRICE = 15
    MIN_NO_PRICE = 15
    MAX_DISAGREEMENT_CENTS = 25
    MAX_FAIR_MARKET_RATIO = 3.0

# ── Risk management ──────────────────────────────────────────────────────

CORRELATION_GROUPS = {
    'gulf_south': ['HOU', 'NOLA', 'DAL', 'OKC'],
    'northeast': ['BOS', 'DC'],
    'pacific': ['SEA', 'SFO'],
    'southeast': ['ATL'],
    'desert': ['PHX'],
    'north_central': ['MIN'],
}
MAX_PER_GROUP = 2
MAX_PER_CITY_DATE = 1
MAX_DAILY_LOSS_CENTS = 500
MAX_WEEKLY_LOSS_CENTS = 1000
CIRCUIT_BREAKER_ALERT_INTERVAL = 3600  # seconds

# ── City × season standard deviations (°F) ──────────────────────────────

CITY_STD_DEV = {
    'PHX':  {'winter': 0.9, 'spring': 1.1, 'summer': 0.8, 'fall': 0.9},
    'SFO':  {'winter': 1.3, 'spring': 1.5, 'summer': 1.1, 'fall': 1.3},
    'SEA':  {'winter': 1.6, 'spring': 1.5, 'summer': 0.9, 'fall': 1.5},
    'DC':   {'winter': 1.5, 'spring': 1.3, 'summer': 1.1, 'fall': 1.3},
    'HOU':  {'winter': 1.3, 'spring': 1.1, 'summer': 0.9, 'fall': 1.1},
    'NOLA': {'winter': 1.3, 'spring': 1.1, 'summer': 0.9, 'fall': 1.1},
    'DAL':  {'winter': 1.5, 'spring': 1.3, 'summer': 0.9, 'fall': 1.3},
    'BOS':  {'winter': 1.5, 'spring': 1.3, 'summer': 1.1, 'fall': 1.3},
    'OKC':  {'winter': 1.6, 'spring': 1.5, 'summer': 1.1, 'fall': 1.5},
    'ATL':  {'winter': 1.3, 'spring': 1.1, 'summer': 0.9, 'fall': 1.1},
    'MIN':  {'winter': 2.0, 'spring': 1.6, 'summer': 1.1, 'fall': 1.5},
}

# ── Known model biases (°F): positive = model runs warm ─────────────────

MODEL_BIAS = {
    ('NOAA', 'PHX'): 0.0,
    ('OpenMeteo_GFS', 'PHX'): +0.5,
    ('OpenMeteo_GFS', 'BOS'): +1.0,
    ('OpenMeteo_ICON', 'HOU'): -0.8,
}

# ── City → Kalshi series ticker ──────────────────────────────────────────

SERIES = {
    'PHX':  'KXHIGHTPHX',
    'SFO':  'KXHIGHTSFO',
    'SEA':  'KXHIGHTSEA',
    'DC':   'KXHIGHTDC',
    'HOU':  'KXHIGHTHOU',
    'NOLA': 'KXHIGHTNOLA',
    'DAL':  'KXHIGHTDAL',
    'BOS':  'KXHIGHTBOS',
    'OKC':  'KXHIGHTOKC',
    'ATL':  'KXHIGHTATL',
    'MIN':  'KXHIGHTMIN',
}

# ── Telegram ─────────────────────────────────────────────────────────────

TG_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TG_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")


# ── Helpers ──────────────────────────────────────────────────────────────

def get_season(date):
    """Return season name for a given date."""
    month = date.month
    if month in (12, 1, 2):
        return 'winter'
    if month in (3, 4, 5):
        return 'spring'
    if month in (6, 7, 8):
        return 'summer'
    return 'fall'


def get_city_std_dev(city, target_date):
    """Get city × season specific standard deviation, with fallback."""
    season = get_season(target_date)
    return CITY_STD_DEV.get(city, {}).get(season, FORECAST_STD_DEV)


def get_correlation_group(city):
    """Return the correlation group name for a city."""
    for group, cities in CORRELATION_GROUPS.items():
        if city in cities:
            return group
    return city
