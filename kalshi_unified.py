#!/usr/bin/env python3
"""
Unified Kalshi Weather Arbitrage Daemon — All 11 cities.

Ensemble forecasting (NOAA + GFS + ICON + ECMWF), enhanced confidence scoring,
position dedup, Telegram alerts. Replaces kalshi_weather.py and kalshi_weather_enhanced.py.
"""
import os
import json, time, math, os, sys, re, base64, traceback
from pathlib import Path
from datetime import datetime, timezone, timedelta

# Virtual environment packages loaded automatically
import requests
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

from weather_providers import build_ensemble, CITY_CONFIGS

# ── Config ──────────────────────────────────────────────────────────────
# PAPER TRADING MODE
PAPER_TRADING = os.getenv("PAPER_TRADING", "true").lower() == "true"  # Load from .env
PAPER_TRADING_NOTIFICATIONS = False  # Set True to receive Telegram alerts for paper trades

CREDS_PATH = "kalshi.json"
LOG_PATH = Path(__file__).parent / "kalshi_unified_log.txt"
STATE_PATH = Path(__file__).parent / "kalshi_unified_state.json"
PNL_PATH = Path(__file__).parent / "kalshi_pnl.json"  # FIXED: Write to standard location for dashboard
PAPER_TRADES_PATH = Path(__file__).parent / "paper_trades.jsonl"  # Paper trading log

KALSHI_BASE = "https://api.elections.kalshi.com"
BACKTEST_PATH = Path(__file__).parent / "kalshi_backtest_log.jsonl"
SETTLEMENT_LOG_PATH = Path(__file__).parent / "kalshi_settlement_log.jsonl"

# Trading params (adjusted for paper trading if enabled)
MAX_CONTRACTS = 8
MAX_COST_PER_TRADE = 500    # cents ($5)
MAX_OPEN_POSITIONS = 20
MAX_DAILY_TRADES = 40
MIN_VOLUME = 10
POLL_INTERVAL = 900          # 15 min
FORECAST_STD_DEV = 1.1       # FIX-001: Reduced from 2.5 to 1.1°F (realistic RMSE)
MIN_PROVIDER_COUNT = 1
MAX_LOG_LINES = 200
MAX_EDGE_CENTS = 60          # edge sanity cap
MAX_SPREAD = 30              # max yes_ask - yes_bid before skipping
NOAA_STALE_HOURS = 6         # NOAA staleness threshold
NOAA_STALE_PENALTY = 0.5     # weight multiplier when stale

# Paper trading mode: loosen filters to capture more opportunities
if PAPER_TRADING:
    MIN_EDGE_CENTS = 10          # Lower from 15 to see more volume
    MIN_YES_PRICE = 5            # Capture cheaper opportunities
    MIN_NO_PRICE = 5             # Same for NO side
    MIN_CONFIDENCE_SCORE = 0.5   # Allow slightly lower confidence
    MODEL_WEIGHT = 0.3           # Keep same Bayesian blend
    MAX_DISAGREEMENT_CENTS = 40  # STEP 2: Increased from 30 to 40 for more volume
    MAX_FAIR_MARKET_RATIO = 3.5  # Allow bigger edges
else:
    # Live trading: conservative settings
    MIN_EDGE_CENTS = 15          # FIX-003: Increased from 10 to 15 (more conservative)
    MIN_CONFIDENCE_SCORE = 0.6
    MODEL_WEIGHT = 0.3           # Bayesian blend: 30% model, 70% market
    MIN_YES_PRICE = 15           # Don't buy YES under 15¢ (cheap tail bets)
    MIN_NO_PRICE = 15            # Don't buy NO under 15¢ (i.e., don't sell YES above 85¢)
    MAX_DISAGREEMENT_CENTS = 25  # Skip if |blended_fair - market| > 25¢
    MAX_FAIR_MARKET_RATIO = 3.0  # Skip if fair/market ratio > 3x

# ── TICK-014: Risk management ───────────────────────────────────────────
CORRELATION_GROUPS = {
    'gulf_south': ['HOU', 'NOLA', 'DAL', 'OKC'],
    'northeast': ['BOS', 'DC'],
    'pacific': ['SEA', 'SFO'],
    'southeast': ['ATL'],
    'desert': ['PHX'],
    'north_central': ['MIN'],
}
MAX_PER_GROUP = 2           # max simultaneous positions per correlation group
MAX_PER_CITY_DATE = 1       # max 1 position per city per settlement date
MAX_DAILY_LOSS_CENTS = 500  # $5 daily loss limit
MAX_WEEKLY_LOSS_CENTS = 1000  # $10 weekly loss limit
CIRCUIT_BREAKER_ALERT_INTERVAL = 3600  # seconds (1 hour between alerts)

BACKTEST_PATH = Path(__file__).parent / "kalshi_backtest_log.jsonl"

# ── TICK-013: City×season standard deviations ───────────────────────────
CITY_STD_DEV = {
    'PHX': {'winter': 0.9, 'spring': 1.1, 'summer': 0.8, 'fall': 0.9},
    'SFO': {'winter': 1.3, 'spring': 1.5, 'summer': 1.1, 'fall': 1.3},
    'SEA': {'winter': 1.6, 'spring': 1.5, 'summer': 0.9, 'fall': 1.5},
    'DC':  {'winter': 1.5, 'spring': 1.3, 'summer': 1.1, 'fall': 1.3},
    'HOU': {'winter': 1.3, 'spring': 1.1, 'summer': 0.9, 'fall': 1.1},
    'NOLA': {'winter': 1.3, 'spring': 1.1, 'summer': 0.9, 'fall': 1.1},
    'DAL': {'winter': 1.5, 'spring': 1.3, 'summer': 0.9, 'fall': 1.3},
    'BOS': {'winter': 1.5, 'spring': 1.3, 'summer': 1.1, 'fall': 1.3},
    'OKC': {'winter': 1.6, 'spring': 1.5, 'summer': 1.1, 'fall': 1.5},
    'ATL': {'winter': 1.3, 'spring': 1.1, 'summer': 0.9, 'fall': 1.1},
    'MIN': {'winter': 2.0, 'spring': 1.6, 'summer': 1.1, 'fall': 1.5},
}

def get_season(date):
    """Return season (winter/spring/summer/fall) for a given date."""
    month = date.month
    if month in (12, 1, 2): return 'winter'
    if month in (3, 4, 5): return 'spring'
    if month in (6, 7, 8): return 'summer'
    return 'fall'

def get_city_std_dev(city, target_date):
    """Get city×season-specific standard deviation, fallback to global default."""
    season = get_season(target_date)
    return CITY_STD_DEV.get(city, {}).get(season, FORECAST_STD_DEV)

# ── TICK-014: Known model biases (°F): positive = model runs warm ───────
MODEL_BIAS = {
    ('NOAA', 'PHX'): 0.0,
    ('OpenMeteo_GFS', 'PHX'): +0.5,
    ('OpenMeteo_GFS', 'BOS'): +1.0,
    ('OpenMeteo_ICON', 'HOU'): -0.8,
    # Populate as settlement data accumulates
}

# City → Kalshi series ticker
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

# ── Env / Telegram ──────────────────────────────────────────────────────
def _load_env():
    env_path = Path(__file__).parent / ".env"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())

_load_env()

# TICK-024a: Load Kalshi-specific Telegram bot credentials
KALSHI_TG_CREDS_PATH = Path(os.getenv("telegram_kalshi.json_PATH", "./telegram_kalshi.json"))
# Load Telegram credentials from environment variables
TG_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TG_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
if TG_BOT_TOKEN:
    print("✓ Telegram notifications enabled")
else:
    print("ℹ Telegram notifications disabled (no bot token)")

def tg_notify(msg):
    """Legacy simple notification function. Use structured functions instead."""
    try:
        requests.post(
            f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage",
            json={"chat_id": TG_CHAT_ID, "text": msg, "parse_mode": "HTML"},
            timeout=10,
        )
    except Exception as e:
        # FIX-016: Log Telegram notification failures (non-critical, continue execution)
        print(f"[WARN] Telegram notification failed: {e}")

# ── TICK-024a: Enhanced Telegram Notifications ──────────────────────────

def tg_notify_trade_opened(trade_data):
    """Rich notification for trade execution with structured formatting.
    
    Args:
        trade_data: dict with keys:
            - ticker: contract ticker
            - side: 'yes' or 'no'
            - count: number of contracts
            - price: price per contract in cents
            - description: human-readable contract description
            - forecast: forecast temperature
            - provider_count: number of forecast providers
            - confidence: confidence score (0-1)
            - edge: adjusted edge in cents
            - cost: total cost in cents
            - is_paper: True if paper trade
    """
    # Skip paper trade notifications if disabled
    if trade_data.get('is_paper') and not PAPER_TRADING_NOTIFICATIONS:
        return
    
    emoji = "📝" if trade_data.get('is_paper') else "✅"
    trade_type = "<b>Paper Trade</b>" if trade_data.get('is_paper') else "<b>Trade Executed</b>"
    
    msg = f"""{emoji} {trade_type}

<b>Contract:</b> {trade_data['ticker']}
<b>Side:</b> {trade_data['side'].upper()}
<b>Quantity:</b> {trade_data['count']}x @ {trade_data['price']}¢

<b>Details:</b>
• {trade_data['description']}
• Forecast: {trade_data['forecast']:.1f}°F ({trade_data['provider_count']} providers)

<b>Analysis:</b>
• Confidence: {trade_data['confidence']:.0%}
• Edge: {trade_data['edge']:.1f}¢
• Cost: ${trade_data['cost']/100:.2f}"""

    try:
        requests.post(
            f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage",
            json={"chat_id": TG_CHAT_ID, "text": msg, "parse_mode": "HTML"},
            timeout=10,
        )
    except Exception as e:
        print(f"[WARN] Telegram trade notification failed: {e}")


def tg_notify_settlement(settlement_data):
    """Rich notification for position settlement with outcome details.
    
    Args:
        settlement_data: dict with keys:
            - ticker: contract ticker
            - won: True if won, False if lost
            - pnl_cents: P&L in cents
            - total_pnl_cents: total cumulative P&L in cents
            - actual_temp: actual observed temperature (optional)
            - forecast: forecast temperature (optional)
            - is_paper: True if paper trade
    """
    # Skip paper trade notifications if disabled
    if settlement_data.get('is_paper') and not PAPER_TRADING_NOTIFICATIONS:
        return
    won = settlement_data['won']
    emoji = "🎯" if won else "📉"
    outcome = "WIN" if won else "LOSS"
    pnl = settlement_data['pnl_cents']
    
    trade_type = "Paper Position" if settlement_data.get('is_paper') else "Position"
    
    msg = f"""{emoji} <b>{trade_type} Settled</b>

<b>Contract:</b> {settlement_data['ticker']}
<b>Outcome:</b> {outcome}
<b>P&L:</b> ${pnl/100:+.2f}"""

    # Add temperature comparison if available
    if settlement_data.get('actual_temp') and settlement_data.get('forecast'):
        actual = settlement_data['actual_temp']
        forecast = settlement_data['forecast']
        error = abs(actual - forecast)
        msg += f"\n\n<b>Temperatures:</b>\n• Forecast: {forecast:.1f}°F\n• Actual: {actual:.1f}°F\n• Error: {error:.1f}°F"
    elif settlement_data.get('actual_temp'):
        msg += f"\n• Actual temp: {settlement_data['actual_temp']:.1f}°F"
    
    # Add cumulative P&L
    total_pnl = settlement_data['total_pnl_cents']
    pnl_emoji = "💰" if total_pnl >= 0 else "⚠️"
    trade_label = "Paper" if settlement_data.get('is_paper') else "Total"
    msg += f"\n\n{pnl_emoji} <b>{trade_label} P&L:</b> ${total_pnl/100:+.2f}"

    try:
        requests.post(
            f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage",
            json={"chat_id": TG_CHAT_ID, "text": msg, "parse_mode": "HTML"},
            timeout=10,
        )
    except Exception as e:
        print(f"[WARN] Telegram settlement notification failed: {e}")


def tg_notify_daily_summary(summary_data):
    """Rich notification for end-of-day summary.
    
    Args:
        summary_data: dict with keys:
            - date: date string (YYYY-MM-DD)
            - trades: number of trades
            - wins: number of winning trades
            - losses: number of losing trades
            - pnl_cents: daily P&L in cents
            - total_pnl_cents: total cumulative P&L
            - open_positions: number of open positions
            - balance: current balance in cents
            - is_paper: True if paper trading stats
    """
    trades = summary_data['trades']
    wins = summary_data['wins']
    losses = summary_data['losses']
    win_rate = (wins / trades * 100) if trades > 0 else 0
    pnl = summary_data['pnl_cents']
    
    pnl_emoji = "📈" if pnl >= 0 else "📉"
    trade_type = "Paper Trading" if summary_data.get('is_paper') else "Trading"
    
    msg = f"""📅 <b>Daily {trade_type} Summary</b>

<b>Date:</b> {summary_data['date']}

<b>Activity:</b>
• Trades: {trades}
• Record: {wins}W-{losses}L ({win_rate:.0f}%)
• Daily P&L: ${pnl/100:+.2f} {pnl_emoji}

<b>Portfolio:</b>
• Open positions: {summary_data['open_positions']}
• Balance: ${summary_data['balance']/100:.2f}
• Total P&L: ${summary_data['total_pnl_cents']/100:+.2f}"""

    try:
        requests.post(
            f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage",
            json={"chat_id": TG_CHAT_ID, "text": msg, "parse_mode": "HTML"},
            timeout=10,
        )
    except Exception as e:
        print(f"[WARN] Telegram daily summary failed: {e}")


def tg_notify_system_alert(alert_data):
    """Rich notification for system alerts and errors.
    
    Args:
        alert_data: dict with keys:
            - level: 'info', 'warning', 'error', or 'critical'
            - title: alert title
            - message: alert message
            - details: optional additional details
    """
    level = alert_data.get('level', 'info').lower()
    
    # Map alert level to emoji
    emoji_map = {
        'info': 'ℹ️',
        'warning': '⚠️',
        'error': '🔴',
        'critical': '🚨'
    }
    emoji = emoji_map.get(level, 'ℹ️')
    
    msg = f"""{emoji} <b>{alert_data['title']}</b>

{alert_data['message']}"""

    if alert_data.get('details'):
        msg += f"\n\n<b>Details:</b>\n{alert_data['details']}"

    try:
        requests.post(
            f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage",
            json={"chat_id": TG_CHAT_ID, "text": msg, "parse_mode": "HTML"},
            timeout=10,
        )
    except Exception as e:
        print(f"[WARN] Telegram system alert failed: {e}")

# ── Credentials ─────────────────────────────────────────────────────────
creds = json.load(open(CREDS_PATH))
KEY_ID = creds['api_key_id']
PRIVATE_KEY = serialization.load_pem_private_key(creds['private_key'].encode(), password=None)

# ── Ensemble ────────────────────────────────────────────────────────────
weather_ensemble = build_ensemble()

# ── Circuit breaker alert throttling ────────────────────────────────────
_last_circuit_breaker_alert = 0  # timestamp of last circuit breaker alert

# ── NOAA staleness check ────────────────────────────────────────────────
def get_noaa_update_age_hours(city_cfg):
    """Return hours since NOAA last updated this grid's forecast, or None on error."""
    try:
        office = city_cfg['noaa_office']
        gx, gy = city_cfg['noaa_grid_x'], city_cfg['noaa_grid_y']
        url = f"https://api.weather.gov/gridpoints/{office}/{gx},{gy}/forecast"
        r = requests.get(url, headers={'User-Agent': 'KaelWeatherBot/2.0'}, timeout=10)
        r.raise_for_status()
        update_str = r.json().get('properties', {}).get('updateTime')
        if update_str:
            update_dt = datetime.fromisoformat(update_str.replace('Z', '+00:00'))
            age = datetime.now(timezone.utc) - update_dt
            return age.total_seconds() / 3600.0
    except Exception as e:
        # FIX-016: Log NOAA API errors (non-critical, return None)
        print(f"[WARN] NOAA update age check failed for {city_cfg.get('name', '?')}: {e}")
    return None

def get_poll_interval():
    """TICK-014: Shorter intervals around model update times."""
    hour = datetime.now().hour
    # NOAA updates ~4AM, 10AM, 4PM, 10PM local; GFS every 6 hours
    if hour in (4, 5, 10, 11, 16, 17, 22, 23):
        return 300   # 5 min during model updates
    elif hour in (6, 7, 12, 13, 18, 19):
        return 600   # 10 min shortly after
    else:
        return 1800  # 30 min during quiet periods


def get_staleness_adjusted_forecast(city_cfg, target_date, city_code=None):
    """Get ensemble forecast with NOAA weight reduced if stale.
    
    FIX-018: Uses weight_overrides parameter instead of mutating global state.
    Replaces previous FIX-012 try/finally pattern with cleaner immutable approach.
    """
    # Check NOAA staleness first
    noaa_age = get_noaa_update_age_hours(city_cfg)
    noaa_stale = noaa_age is not None and noaa_age > NOAA_STALE_HOURS

    # FIX-018: Pass weight override instead of mutating weather_ensemble.providers
    weight_overrides = None
    if noaa_stale:
        weight_overrides = {'NOAA': NOAA_STALE_PENALTY}  # Apply penalty multiplier
        log(f"  NOAA stale ({noaa_age:.1f}h) — applying {NOAA_STALE_PENALTY}x weight penalty")

    ensemble_temp, ensemble_details = weather_ensemble.get_ensemble_forecast(
        city_cfg, target_date, city_code=city_code, model_bias=MODEL_BIAS, 
        weight_overrides=weight_overrides)

    # Record staleness info
    if ensemble_details:
        ensemble_details['noaa_age_hours'] = round(noaa_age, 1) if noaa_age is not None else None
        ensemble_details['noaa_stale'] = noaa_stale

    return ensemble_temp, ensemble_details

# ── Backtest JSONL logger ───────────────────────────────────────────────
def log_backtest(entry):
    """Append one JSON line to the backtest log."""
    try:
        with open(BACKTEST_PATH, 'a') as f:
            f.write(json.dumps(entry, default=str) + "\n")
    except Exception as e:
        # FIX-016: Log backtest write failures (non-critical)
        print(f"[WARN] Failed to write backtest entry: {e}")

# ── Paper Trading Logger ────────────────────────────────────────────────
def log_paper_trade(entry):
    """Append one JSON line to the paper trades log."""
    try:
        with open(PAPER_TRADES_PATH, 'a') as f:
            f.write(json.dumps(entry, default=str) + "\n")
    except Exception as e:
        print(f"[WARN] Failed to write paper trade entry: {e}")

# ── Logging ─────────────────────────────────────────────────────────────
def log(msg):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    try:
        with open(LOG_PATH, 'a') as f:
            f.write(line + "\n")
        with open(LOG_PATH, 'r') as f:
            lines = f.readlines()
        if len(lines) > MAX_LOG_LINES:
            with open(LOG_PATH, 'w') as f:
                f.writelines(lines[-MAX_LOG_LINES:])
    except Exception as e:
        # FIX-016: Log file write failures (already printed to stdout)
        print(f"[ERROR] Log file write/rotation failed: {e}")

# ── State / PnL ────────────────────────────────────────────────────────
def load_state():
    if STATE_PATH.exists():
        return json.load(open(STATE_PATH))
    return {"positions": [], "daily_trades": 0, "last_trade_date": "", "total_pnl_cents": 0}

def save_state(state):
    with open(STATE_PATH, 'w') as f:
        json.dump(state, f, indent=2)

def load_pnl():
    if PNL_PATH.exists():
        return json.load(open(PNL_PATH))
    return {"weeks": {}, "daily": {}}

def save_pnl(pnl):
    with open(PNL_PATH, 'w') as f:
        json.dump(pnl, f, indent=2)

def record_pnl(amount_cents, ticker):
    pnl = load_pnl()
    today = datetime.now().strftime("%Y-%m-%d")
    week_key = datetime.now().strftime("%Y-W%U")
    for bucket_key, bucket_name in [(today, "daily"), (week_key, "weeks")]:
        if bucket_key not in pnl[bucket_name]:
            pnl[bucket_name][bucket_key] = {"pnl_cents": 0, "trades": 0, "wins": 0, "losses": 0}
        pnl[bucket_name][bucket_key]["pnl_cents"] += amount_cents
        pnl[bucket_name][bucket_key]["trades"] += 1
        if amount_cents > 0:
            pnl[bucket_name][bucket_key]["wins"] += 1
        else:
            pnl[bucket_name][bucket_key]["losses"] += 1
    save_pnl(pnl)

# ── Kalshi API ──────────────────────────────────────────────────────────
def kalshi_request(method, path, body=None):
    ts = str(int(time.time() * 1000))
    msg = (ts + method.upper() + path).encode()
    sig = PRIVATE_KEY.sign(
        msg,
        padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.DIGEST_LENGTH),
        hashes.SHA256(),
    )
    headers = {
        'KALSHI-ACCESS-KEY': KEY_ID,
        'KALSHI-ACCESS-SIGNATURE': base64.b64encode(sig).decode(),
        'KALSHI-ACCESS-TIMESTAMP': ts,
        'Content-Type': 'application/json',
    }
    url = KALSHI_BASE + path
    if method.upper() == 'GET':
        r = requests.get(url, headers=headers, timeout=15)
    elif method.upper() == 'POST':
        r = requests.post(url, headers=headers, json=body, timeout=15)
    else:
        r = requests.request(method.upper(), url, headers=headers, json=body, timeout=15)
    return r.json()

def get_real_balance():
    """Always fetch real Kalshi account balance (even in paper mode)."""
    try:
        return kalshi_request('GET', '/trade-api/v2/portfolio/balance').get('balance', 0)
    except Exception as e:
        log(f"ERROR fetching real balance: {e}")
        return 0

def get_balance():
    # SAFETY: Check paper trading mode
    if PAPER_TRADING:
        from paper_trading_safety import paper_get_balance
        return paper_get_balance()
    return get_real_balance()

def get_positions():
    """Returns (event_positions, open_event_tickers)."""
    # SAFETY: Check paper trading mode
    if PAPER_TRADING:
        from paper_trading_safety import paper_get_positions
        return paper_get_positions()
    
    data = kalshi_request('GET', '/trade-api/v2/portfolio/positions')
    event_positions = data.get('event_positions', [])
    open_tickers = set()
    for ep in event_positions:
        if ep.get('event_exposure', 0) > 0:
            open_tickers.add(ep.get('event_ticker', ''))
    return event_positions, open_tickers

def place_order(ticker, side, count, price_cents):
    # SAFETY: Check paper trading mode
    if PAPER_TRADING:
        from paper_trading_safety import paper_place_order
        return paper_place_order(ticker, side, count, price_cents)
    
    body = {
        "ticker": ticker,
        "action": "buy",
        "side": side,
        "type": "limit",
        "count": count,
        "yes_price": price_cents if side == 'yes' else None,
        "no_price": price_cents if side == 'no' else None,
    }
    body = {k: v for k, v in body.items() if v is not None}
    return kalshi_request('POST', '/trade-api/v2/portfolio/orders', body)

# ── Math / Confidence ───────────────────────────────────────────────────
def detect_contract_type(ticker):
    """Detect if contract is T (threshold) or B (bracket) from ticker."""
    if '-T' in ticker:
        return 'threshold'
    elif '-B' in ticker:
        return 'bracket'
    return None

def normal_cdf(x):
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))

def market_adjusted_fair(model_p, market_p, model_weight=MODEL_WEIGHT):
    """Blend model probability with market price using log-odds (Bayesian).
    
    Args:
        model_p: Model's probability estimate (0-1)
        market_p: Market-implied probability (0-1)
        model_weight: Weight given to model (default 0.3 = 30% model, 70% market)
        
    Returns:
        Blended probability (0-1)
    """
    # Clamp to avoid log(0)
    market_p = max(0.02, min(0.98, market_p))
    model_p = max(0.02, min(0.98, model_p))
    
    # Log-odds blending (proper Bayesian in log-odds space)
    def logit(p): return math.log(p / (1 - p))
    def inv_logit(x): return 1 / (1 + math.exp(-x))
    
    blended_logit = model_weight * logit(model_p) + (1 - model_weight) * logit(market_p)
    return inv_logit(blended_logit)

def calculate_confidence_score(ensemble_details: dict, forecast_temp: float, std_dev: float) -> float:
    """Calculate confidence score based on provider agreement and count.
    
    Args:
        ensemble_details: Ensemble forecast details with individual forecasts
        forecast_temp: Forecast temperature in Fahrenheit (float)
        std_dev: Standard deviation for the forecast
        
    Returns:
        Confidence score between 0.0 and 1.0
    """
    if not ensemble_details or ensemble_details.get('provider_count', 0) < MIN_PROVIDER_COUNT:
        return 0.0
    individual = ensemble_details.get('individual_forecasts', {})
    if len(individual) < 2:
        return 0.7  # single provider base confidence
    forecasts = list(individual.values())
    mean_f = sum(forecasts) / len(forecasts)
    forecast_std = math.sqrt(sum((f - mean_f) ** 2 for f in forecasts) / len(forecasts))
    agreement_score = max(0.5, 1.0 - (forecast_std / 5.0))
    provider_score = min(1.0, len(individual) / 3.0)
    raw_confidence = agreement_score * 0.7 + provider_score * 0.3
    # FIX-015: Clamp confidence to valid range [0,1]
    confidence = min(1.0, max(0.0, raw_confidence))
    return confidence

def fair_probability_enhanced(forecast_temp: float, ensemble_details: dict, floor_strike, cap_strike, 
                             city=None, target_date=None, std=FORECAST_STD_DEV, days_ahead=1,
                             strike_type=None) -> float:
    """Calculate fair probability using city×season-specific standard deviation.
    
    Args:
        forecast_temp: Forecast temperature in Fahrenheit (must be float)
        ensemble_details: Ensemble forecast details
        floor_strike: Floor strike price (or None)
        cap_strike: Cap strike price (or None)
        city: City code for std dev lookup
        target_date: Target date for seasonal adjustment
        std: Base standard deviation (default from FORECAST_STD_DEV)
        days_ahead: Days ahead for lead-time scaling
        strike_type: Contract strike type ("less", "greater", "between")
        
    Returns:
        Fair probability between 0.0 and 1.0
    """
    if not forecast_temp:
        return 0.5
    
    # Use city×season std dev if available
    if city and target_date:
        std = get_city_std_dev(city, target_date)
    
    confidence = calculate_confidence_score(ensemble_details, forecast_temp, std)
    
    # TICK-015 Fix 5: Same-day forecasts are ~2x more accurate, reduce std dev
    # BA analysis: same-day RMSE ~1.5°F vs multi-day ~3-5°F
    if days_ahead == 0:
        decay_factor = 0.5  # Same-day: halve std dev (2x more accurate)
    elif days_ahead == 1:
        decay_factor = 0.75  # Next-day: modest reduction
    else:
        decay_factor = 1.0 + 0.35 * (days_ahead - 1)  # 2+ days: increase uncertainty
    
    # FIX-007: Reduced confidence multiplier (was 1.0-2.0x, now 1.0-1.2x)
    confidence_mult = 1.2 - 0.2 * confidence
    adjusted_std = std * confidence_mult * decay_factor
    
    # FIX-015: Validate adjusted_std to prevent division by zero or negative values
    if adjusted_std <= 0:
        log(f"ERROR: Invalid adjusted_std={adjusted_std:.4f} (std={std}, conf_mult={confidence_mult:.3f}, decay={decay_factor:.3f}), using default 1.0")
        adjusted_std = 1.0
    
    # FIX-020: Use strike_type field for correct contract semantics
    if strike_type == 'less':
        # Lower-T: YES = temp < cap_strike
        return normal_cdf((cap_strike - forecast_temp) / adjusted_std)
    elif strike_type == 'greater':
        # Upper-T: YES = temp > floor_strike
        return 1.0 - normal_cdf((floor_strike - forecast_temp) / adjusted_std)
    elif strike_type == 'between':
        # B-contract: YES = floor_strike < temp < cap_strike
        z1 = (floor_strike - forecast_temp) / adjusted_std
        z2 = (cap_strike - forecast_temp) / adjusted_std
        return normal_cdf(z2) - normal_cdf(z1)
    else:
        # FIX-020: Missing or invalid strike_type
        log(f"ERROR: Unknown strike_type={strike_type} for floor={floor_strike} cap={cap_strike}, returning 0.5")
        return 0.5

def kelly_size(fair_p, market_price_cents, bankroll_cents, fraction=0.25):
    """
    Quarter-Kelly sizing for binary contracts.
    
    Args:
        fair_p: Fair probability (0-1)
        market_price_cents: Cost per contract in cents
        bankroll_cents: Available bankroll in cents
        fraction: Kelly fraction (0.25 = quarter-Kelly)
    
    Returns:
        Number of contracts to buy (capped by MAX_CONTRACTS)
    """
    if fair_p <= 0 or fair_p >= 1 or market_price_cents <= 0:
        return 0
    
    cost = market_price_cents
    payout = 100 - cost  # profit if win
    b = payout / cost    # odds ratio
    q = 1 - fair_p
    
    f_star = (fair_p * b - q) / b  # full Kelly fraction
    f_safe = max(0, f_star * fraction)
    
    max_contracts = int((bankroll_cents * f_safe) / cost)
    return max(0, min(max_contracts, MAX_CONTRACTS))

# ── Date parsing ────────────────────────────────────────────────────────
MONTH_MAP = {'jan':1,'feb':2,'mar':3,'apr':4,'may':5,'jun':6,
             'jul':7,'aug':8,'sep':9,'oct':10,'nov':11,'dec':12}

def parse_event_date(title):
    """Parse target date from event title. Returns datetime or None."""
    title_lower = title.lower()
    # Try "Feb 12", "January 5", etc.
    m = re.search(r'(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\w*\s+(\d+)', title_lower)
    if m:
        month_num = MONTH_MAP.get(m.group(1)[:3])
        day_num = int(m.group(2))
        if month_num:
            now = datetime.now()
            year = now.year if month_num >= now.month else now.year + 1
            try:
                return datetime(year, month_num, day_num)
            except ValueError:
                pass
    now = datetime.now()
    if 'today' in title_lower:
        return now
    if 'tomorrow' in title_lower:
        return now + timedelta(days=1)
    return None

# ── TICK-014: Risk management helpers ──────────────────────────────────
def get_correlation_group(city):
    """Return the correlation group name for a city, or the city itself if standalone."""
    for group, cities in CORRELATION_GROUPS.items():
        if city in cities:
            return group
    return city  # standalone

def check_circuit_breaker(pnl_data, state):
    """Returns (should_trade, reason) tuple based on daily/weekly loss limits.
    
    TICK-015: Now includes unrealized exposure from today's open positions.
    """
    today = datetime.now().strftime("%Y-%m-%d")
    today_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    week_key = datetime.now().strftime("%Y-W%U")
    
    daily = pnl_data.get('daily', {}).get(today, {})
    weekly = pnl_data.get('weeks', {}).get(week_key, {})
    
    daily_pnl = daily.get('pnl_cents', 0)
    weekly_pnl = weekly.get('pnl_cents', 0)
    
    # TICK-015: Include unrealized exposure from today's open positions
    # Worst case: all open positions opened today lose 100%
    today_exposure = sum(
        p.get('count', 0) * p.get('price', 0)
        for p in state.get('positions', [])
        if p.get('trade_time', '').startswith(today) or 
           p.get('trade_time', '').startswith(today_utc)
    )
    
    effective_daily = daily_pnl - today_exposure  # worst case scenario
    
    if effective_daily <= -MAX_DAILY_LOSS_CENTS:
        return False, f"Daily loss limit (incl. ${today_exposure/100:.2f} at-risk): ${effective_daily/100:.2f}"
    if weekly_pnl - today_exposure <= -MAX_WEEKLY_LOSS_CENTS:
        return False, f"Weekly loss limit (incl. exposure): ${(weekly_pnl - today_exposure)/100:.2f}"
    return True, ""

# ── Opportunity scanner ─────────────────────────────────────────────────
def _bt(ticker, city, forecast, ensemble_details, confidence, fair_cents,
        yes_ask, yes_bid, floor_s, cap_s, raw_edge, adjusted_edge,
        side, price, action, skip_reason=None, days_ahead=None, std_dev_used=None, provider_spread=None,
        model_fair=None, market_price=None, blended_fair=None, strike_type=None):
    """Build and write a backtest JSONL entry."""
    log_backtest({
        "ts": datetime.now().isoformat(), "ticker": ticker, "city": city,
        "forecast": forecast, "ensemble_details": ensemble_details,
        "confidence": round(confidence, 4) if confidence else None,
        "fair_cents": fair_cents,
        "market_yes_ask": yes_ask, "market_yes_bid": yes_bid,
        "floor_strike": floor_s, "cap_s": cap_s,
        "strike_type": strike_type,  # FIX-020
        "raw_edge": raw_edge,
        "adjusted_edge": round(adjusted_edge, 2) if adjusted_edge is not None else None,
        "side": side, "price": price, "action": action, "skip_reason": skip_reason,
        "days_ahead": days_ahead,  # TICK-012
        "std_dev_used": round(std_dev_used, 2) if std_dev_used else None,
        "provider_spread": round(provider_spread, 2) if provider_spread is not None else None,  # TICK-012
        "model_fair": model_fair,  # TICK-015
        "market_price": market_price,  # TICK-015
        "blended_fair": blended_fair,  # TICK-015
    })

def find_opportunities():
    """Find trading opportunities across all cities with input validation.
    
    Returns:
        List of opportunity dictionaries
    """
    opportunities = []
    _forecast_cache = {}  # (city, date_str) -> (forecast_temp: float, ensemble_details, confidence)
    
    # FIX-019: Validate SERIES and CITY_CONFIGS are not empty
    if not SERIES:
        log("ERROR: SERIES is empty - no cities configured")
        return []
    if not CITY_CONFIGS:
        log("ERROR: CITY_CONFIGS is empty - configuration missing")
        return []
    
    for city, series in SERIES.items():
        # FIX-019: Validate city exists in CITY_CONFIGS
        city_cfg = CITY_CONFIGS.get(city)
        if not city_cfg:
            log(f"ERROR: No city config for {city} in CITY_CONFIGS, skipping")
            continue
        try:
            data = kalshi_request('GET', f'/trade-api/v2/events?series_ticker={series}&status=open&with_nested_markets=true&limit=5')
            events = data.get('events', [])
            for event in events:
                title = event.get('title', '')
                target_date = parse_event_date(title)
                if target_date is None:
                    continue

                # TICK-013: Calculate days ahead for lead-time scaling
                days_ahead = max(0, (target_date.date() - datetime.now().date()).days)
                city_std = get_city_std_dev(city, target_date)

                cache_key = (city, target_date.strftime("%Y-%m-%d"))
                if cache_key in _forecast_cache:
                    forecast_temp, ensemble_details, confidence = _forecast_cache[cache_key]
                else:
                    ensemble_temp, ensemble_details = get_staleness_adjusted_forecast(city_cfg, target_date, city_code=city)
                    if ensemble_temp is None:
                        _forecast_cache[cache_key] = (None, None, None)
                        continue
                    # FIX-017: Ensure forecast_temp is ALWAYS float (explicit cast for type safety)
                    forecast_temp: float = float(ensemble_temp)
                    
                    # FIX-019: Validate ensemble_details has required keys
                    if not ensemble_details or not isinstance(ensemble_details, dict):
                        log(f"ERROR: Invalid ensemble_details for {city} on {target_date.strftime('%Y-%m-%d')}")
                        _forecast_cache[cache_key] = (None, None, None)
                        continue
                    if 'individual_forecasts' not in ensemble_details:
                        log(f"WARN: ensemble_details missing 'individual_forecasts' for {city}")
                    
                    confidence = calculate_confidence_score(ensemble_details, forecast_temp, city_std)
                    _forecast_cache[cache_key] = (forecast_temp, ensemble_details, confidence)
                
                # FIX-019: Validate forecast_temp and confidence after cache retrieval
                if forecast_temp is None or confidence < MIN_CONFIDENCE_SCORE:
                    continue
                
                # FIX-019: Additional type safety check for forecast_temp
                if not isinstance(forecast_temp, (int, float)):
                    log(f"ERROR: forecast_temp has invalid type {type(forecast_temp)} for {city}")
                    continue
                forecast_temp = float(forecast_temp)  # Ensure it's float, not int

                # TICK-012: Calculate provider spread for filtering and logging
                individual = ensemble_details.get('individual_forecasts', {})
                provider_spread = None
                if len(individual) >= 2:
                    forecasts = list(individual.values())
                    provider_spread = max(forecasts) - min(forecasts)
                    # TICK-012: Provider spread hard filter — skip if providers disagree by >6°F
                    if provider_spread > 6.0:
                        log(f"  SKIP {city} {target_date.strftime('%Y-%m-%d')} — provider spread {provider_spread:.1f}°F > 6°F")
                        # Log to backtest for the first market in this event (we'll see it when we iterate)
                        continue

                event_ticker = event.get('event_ticker', '')
                for m in event.get('markets', []):
                    yes_ask = m.get('yes_ask', 0)
                    yes_bid = m.get('yes_bid', 0)
                    vol = m.get('volume', 0)
                    floor_s = m.get('floor_strike')
                    cap_s = m.get('cap_strike')
                    strike_type = m.get('strike_type')  # FIX-020: Extract strike_type ("less", "greater", "between")
                    ticker = m['ticker']
                    
                    # FIX-019: Validate strike values are positive numbers when present
                    if floor_s is not None:
                        if not isinstance(floor_s, (int, float)) or floor_s < 0:
                            log(f"ERROR: Invalid floor_strike={floor_s} for {ticker}, skipping")
                            continue
                    if cap_s is not None:
                        if not isinstance(cap_s, (int, float)) or cap_s < 0:
                            log(f"ERROR: Invalid cap_strike={cap_s} for {ticker}, skipping")
                            continue
                    
                    # FIX-019: At least one strike must be defined
                    if floor_s is None and cap_s is None:
                        log(f"ERROR: Both strikes are None for {ticker}, skipping")
                        continue
                    
                    # FIX-020: Validate strike_type field
                    if strike_type not in ('less', 'greater', 'between'):
                        log(f"ERROR: Invalid or missing strike_type={strike_type} for {ticker}, skipping")
                        continue
                    
                    # FIX-010: Detect and log contract type
                    contract_type = detect_contract_type(ticker)
                    if contract_type:
                        log(f"  {ticker}: contract_type={contract_type} strike_type={strike_type} floor={floor_s} cap={cap_s}")

                    if (yes_ask == 0 and yes_bid == 0) or vol < MIN_VOLUME:
                        continue

                    # Spread check — illiquid market filter
                    spread = (yes_ask - yes_bid) if (yes_ask > 0 and yes_bid > 0) else 0
                    if spread > MAX_SPREAD:
                        log(f"  SKIP {ticker} — spread {spread}c > {MAX_SPREAD}c (illiquid)")
                        _bt(ticker, city, forecast_temp, ensemble_details, confidence,
                            None, yes_ask, yes_bid, floor_s, cap_s, None, None,
                            None, None, "skip", f"spread={spread}", 
                            days_ahead=days_ahead, std_dev_used=city_std, provider_spread=provider_spread,
                            strike_type=strike_type)  # FIX-020
                        continue

                    # FIX-006: Strike proximity filter — skip when forecast too close to strike
                    # Market has informational advantage from real-time observations near strike
                    # STEP 2: Paper trading relaxed to 0.2°F to capture more opportunities
                    proximity_threshold = 0.2 if PAPER_TRADING else 1.5
                    
                    strike_distance = None
                    if floor_s is not None and cap_s is not None:
                        strike_distance = min(abs(forecast_temp - floor_s), abs(forecast_temp - cap_s))
                    elif cap_s is not None:
                        strike_distance = abs(forecast_temp - cap_s)
                    elif floor_s is not None:
                        strike_distance = abs(forecast_temp - floor_s)
                    
                    if strike_distance is not None and strike_distance < proximity_threshold:
                        log(f"  SKIP {ticker} — forecast {forecast_temp:.1f}°F too close to strike (distance={strike_distance:.1f}°F)")
                        _bt(ticker, city, forecast_temp, ensemble_details, confidence,
                            None, yes_ask, yes_bid, floor_s, cap_s, None, None,
                            None, None, "skip", f"strike_proximity={strike_distance:.1f}",
                            days_ahead=days_ahead, std_dev_used=city_std, provider_spread=provider_spread,
                            strike_type=strike_type)  # FIX-020
                        continue

                    # TICK-015: Get model probability (before blending)
                    fair_p = fair_probability_enhanced(forecast_temp, ensemble_details, floor_s, cap_s,
                                                       city=city, target_date=target_date, days_ahead=days_ahead,
                                                       strike_type=strike_type)  # FIX-020
                    model_fair_cents = round(fair_p * 100)

                    # TICK-012: Spread-adjusted edge — subtract half-spread from raw edge
                    half_spread = (yes_ask - yes_bid) / 2 if (yes_ask > 0 and yes_bid > 0) else 0

                    # ── Evaluate YES side ──
                    if yes_ask > 0 and yes_ask < 95:
                        # TICK-015: Price floor check FIRST (catches cheapest tail bets)
                        if yes_ask < MIN_YES_PRICE:
                            _bt(ticker, city, forecast_temp, ensemble_details, confidence,
                                model_fair_cents, yes_ask, yes_bid, floor_s, cap_s,
                                None, None, "yes", yes_ask, "skip", f"yes_price_floor={yes_ask}",
                                days_ahead=days_ahead, std_dev_used=city_std, provider_spread=provider_spread,
                                model_fair=model_fair_cents, market_price=yes_ask, blended_fair=None,
                                strike_type=strike_type)  # FIX-020
                            # FIX-009: Removed 'continue' - fall through to NO evaluation
                        elif yes_ask >= MIN_YES_PRICE:
                            # TICK-015: Early disagreement check (model vs market, before blending)
                            model_disagreement = abs(model_fair_cents - yes_ask)
                            if model_disagreement > MAX_DISAGREEMENT_CENTS:
                                log(f"  SKIP {ticker} YES — model disagreement {model_disagreement}¢ > {MAX_DISAGREEMENT_CENTS}¢ (model={model_fair_cents}¢ market={yes_ask}¢)")
                                _bt(ticker, city, forecast_temp, ensemble_details, confidence,
                                    model_fair_cents, yes_ask, yes_bid, floor_s, cap_s,
                                    None, None, "yes", yes_ask, "skip", f"model_disagreement={model_disagreement}",
                                    days_ahead=days_ahead, std_dev_used=city_std, provider_spread=provider_spread,
                                    model_fair=model_fair_cents, market_price=yes_ask, blended_fair=None,
                                    strike_type=strike_type)  # FIX-020
                                continue
                            
                            # TICK-015: Bayesian market blend
                            market_p_yes = yes_ask / 100.0
                            blended_p = market_adjusted_fair(fair_p, market_p_yes, MODEL_WEIGHT)
                            fair_cents = round(blended_p * 100)
                            
                            # TICK-015: Disagreement filter (using blended fair)
                            disagreement = abs(fair_cents - yes_ask)
                            if disagreement > MAX_DISAGREEMENT_CENTS:
                                log(f"  SKIP {ticker} YES — disagreement {disagreement}¢ > {MAX_DISAGREEMENT_CENTS}¢ (model={model_fair_cents}¢ market={yes_ask}¢ blended={fair_cents}¢)")
                                _bt(ticker, city, forecast_temp, ensemble_details, confidence,
                                    fair_cents, yes_ask, yes_bid, floor_s, cap_s,
                                    None, None, "yes", yes_ask, "skip", f"disagreement={disagreement}",
                                    days_ahead=days_ahead, std_dev_used=city_std, provider_spread=provider_spread,
                                    model_fair=model_fair_cents, market_price=yes_ask, blended_fair=fair_cents,
                                    strike_type=strike_type)  # FIX-020
                                continue
                            
                            # TICK-015: Ratio filter
                            if yes_ask > 0 and fair_cents / yes_ask > MAX_FAIR_MARKET_RATIO:
                                ratio = fair_cents / yes_ask
                                log(f"  SKIP {ticker} YES — ratio {ratio:.1f}x > {MAX_FAIR_MARKET_RATIO}x (model={model_fair_cents}¢ market={yes_ask}¢ blended={fair_cents}¢)")
                                _bt(ticker, city, forecast_temp, ensemble_details, confidence,
                                    fair_cents, yes_ask, yes_bid, floor_s, cap_s,
                                    None, None, "yes", yes_ask, "skip", f"ratio={ratio:.1f}x",
                                    days_ahead=days_ahead, std_dev_used=city_std, provider_spread=provider_spread,
                                    model_fair=model_fair_cents, market_price=yes_ask, blended_fair=fair_cents,
                                    strike_type=strike_type)  # FIX-020
                                continue
                            
                            raw_edge = fair_cents - yes_ask - half_spread
                            adjusted_edge = raw_edge * confidence
                            
                            # TICK-015: Diagnostic logging
                            log(f"  {ticker} YES: model={model_fair_cents}¢ market={yes_ask}¢ blended={fair_cents}¢ edge={adjusted_edge:.1f}¢")
                            
                            if adjusted_edge >= MIN_EDGE_CENTS:
                                if adjusted_edge > MAX_EDGE_CENTS:
                                    log(f"  WARN {ticker} YES adj_edge={adjusted_edge:.0f}c > {MAX_EDGE_CENTS}c — likely stale, skip")
                                    _bt(ticker, city, forecast_temp, ensemble_details, confidence,
                                        fair_cents, yes_ask, yes_bid, floor_s, cap_s,
                                        raw_edge, adjusted_edge, "yes", yes_ask, "skip", f"edge_cap={adjusted_edge:.0f}",
                                        days_ahead=days_ahead, std_dev_used=city_std, provider_spread=provider_spread,
                                        model_fair=model_fair_cents, market_price=yes_ask, blended_fair=fair_cents,
                                        strike_type=strike_type)  # FIX-020
                                else:
                                    _bt(ticker, city, forecast_temp, ensemble_details, confidence,
                                        fair_cents, yes_ask, yes_bid, floor_s, cap_s,
                                        raw_edge, adjusted_edge, "yes", yes_ask, "trade", None,
                                        days_ahead=days_ahead, std_dev_used=city_std, provider_spread=provider_spread,
                                        model_fair=model_fair_cents, market_price=yes_ask, blended_fair=fair_cents,
                                        strike_type=strike_type)  # FIX-020
                                    opportunities.append({
                                        'city': city, 'ticker': ticker, 'event_ticker': event_ticker,
                                        'side': 'yes', 'price': yes_ask,
                                        'fair': fair_cents, 'raw_edge': raw_edge,
                                        'adjusted_edge': adjusted_edge, 'confidence': confidence,
                                        'volume': vol, 'forecast': forecast_temp,
                                        'ensemble_details': ensemble_details,
                                        'floor': floor_s, 'cap': cap_s,
                                        'target_date': target_date.strftime("%Y-%m-%d"),  # TICK-014
                                    })
                            else:
                                _bt(ticker, city, forecast_temp, ensemble_details, confidence,
                                    fair_cents, yes_ask, yes_bid, floor_s, cap_s,
                                    raw_edge, adjusted_edge, "yes", yes_ask, "skip", f"edge_low={adjusted_edge:.1f}",
                                    days_ahead=days_ahead, std_dev_used=city_std, provider_spread=provider_spread,
                                    model_fair=model_fair_cents, market_price=yes_ask, blended_fair=fair_cents,
                                    strike_type=strike_type)  # FIX-020

                    # ── Evaluate NO side ──
                    if yes_bid > 0 and yes_bid > 5:
                        no_price = 100 - yes_bid
                        
                        # TICK-015: Price floor check FIRST (don't sell YES above 85¢ = NO below 15¢)
                        if no_price < MIN_NO_PRICE:
                            _bt(ticker, city, forecast_temp, ensemble_details, confidence,
                                model_fair_cents, yes_ask, yes_bid, floor_s, cap_s,
                                None, None, "no", no_price, "skip", f"no_price_floor={no_price}",
                                days_ahead=days_ahead, std_dev_used=city_std, provider_spread=provider_spread,
                                model_fair=model_fair_cents, market_price=yes_bid, blended_fair=None,
                                strike_type=strike_type)  # FIX-020
                            continue
                        
                        # TICK-015: Early disagreement check (model NO vs market NO, before blending)
                        model_fair_no = 100 - model_fair_cents
                        model_disagreement = abs(model_fair_no - no_price)
                        if model_disagreement > MAX_DISAGREEMENT_CENTS:
                            log(f"  SKIP {ticker} NO — model disagreement {model_disagreement}¢ > {MAX_DISAGREEMENT_CENTS}¢ (model={model_fair_no}¢ market={no_price}¢)")
                            _bt(ticker, city, forecast_temp, ensemble_details, confidence,
                                model_fair_no, yes_ask, yes_bid, floor_s, cap_s,
                                None, None, "no", no_price, "skip", f"model_disagreement={model_disagreement}",
                                days_ahead=days_ahead, std_dev_used=city_std, provider_spread=provider_spread,
                                model_fair=model_fair_no, market_price=no_price, blended_fair=None,
                                strike_type=strike_type)  # FIX-020
                            continue
                        
                        # TICK-015: Bayesian market blend (for NO side, blend against yes_bid)
                        market_p_yes = yes_bid / 100.0
                        blended_p = market_adjusted_fair(fair_p, market_p_yes, MODEL_WEIGHT)
                        fair_cents_yes = round(blended_p * 100)
                        fair_cents_no = 100 - fair_cents_yes
                        
                        # TICK-015: Disagreement filter (check blended NO fair vs NO price)
                        disagreement = abs(fair_cents_no - no_price)
                        if disagreement > MAX_DISAGREEMENT_CENTS:
                            log(f"  SKIP {ticker} NO — disagreement {disagreement}¢ > {MAX_DISAGREEMENT_CENTS}¢ (model={100-model_fair_cents}¢ market={no_price}¢ blended={fair_cents_no}¢)")
                            _bt(ticker, city, forecast_temp, ensemble_details, confidence,
                                fair_cents_no, yes_ask, yes_bid, floor_s, cap_s,
                                None, None, "no", no_price, "skip", f"disagreement={disagreement}",
                                days_ahead=days_ahead, std_dev_used=city_std, provider_spread=provider_spread,
                                model_fair=100-model_fair_cents, market_price=no_price, blended_fair=fair_cents_no,
                                strike_type=strike_type)  # FIX-020
                            continue
                        
                        # TICK-015: Ratio filter (for NO side)
                        if no_price > 0 and fair_cents_no / no_price > MAX_FAIR_MARKET_RATIO:
                            ratio = fair_cents_no / no_price
                            log(f"  SKIP {ticker} NO — ratio {ratio:.1f}x > {MAX_FAIR_MARKET_RATIO}x (model={100-model_fair_cents}¢ market={no_price}¢ blended={fair_cents_no}¢)")
                            _bt(ticker, city, forecast_temp, ensemble_details, confidence,
                                fair_cents_no, yes_ask, yes_bid, floor_s, cap_s,
                                None, None, "no", no_price, "skip", f"ratio={ratio:.1f}x",
                                days_ahead=days_ahead, std_dev_used=city_std, provider_spread=provider_spread,
                                model_fair=100-model_fair_cents, market_price=no_price, blended_fair=fair_cents_no,
                                strike_type=strike_type)  # FIX-020
                            continue
                        
                        raw_edge = yes_bid - fair_cents_yes - half_spread
                        adjusted_edge = raw_edge * confidence
                        
                        # TICK-015: Diagnostic logging
                        log(f"  {ticker} NO: model={100-model_fair_cents}¢ market={no_price}¢ blended={fair_cents_no}¢ edge={adjusted_edge:.1f}¢")
                        
                        if adjusted_edge >= MIN_EDGE_CENTS:
                            if adjusted_edge > MAX_EDGE_CENTS:
                                log(f"  WARN {ticker} NO adj_edge={adjusted_edge:.0f}c > {MAX_EDGE_CENTS}c — likely stale, skip")
                                _bt(ticker, city, forecast_temp, ensemble_details, confidence,
                                    fair_cents_no, yes_ask, yes_bid, floor_s, cap_s,
                                    raw_edge, adjusted_edge, "no", no_price, "skip", f"edge_cap={adjusted_edge:.0f}",
                                    days_ahead=days_ahead, std_dev_used=city_std, provider_spread=provider_spread,
                                    model_fair=100-model_fair_cents, market_price=no_price, blended_fair=fair_cents_no,
                                    strike_type=strike_type)  # FIX-020
                            else:
                                _bt(ticker, city, forecast_temp, ensemble_details, confidence,
                                    fair_cents_no, yes_ask, yes_bid, floor_s, cap_s,
                                    raw_edge, adjusted_edge, "no", no_price, "trade", None,
                                    days_ahead=days_ahead, std_dev_used=city_std, provider_spread=provider_spread,
                                    model_fair=100-model_fair_cents, market_price=no_price, blended_fair=fair_cents_no,
                                    strike_type=strike_type)  # FIX-020
                                opportunities.append({
                                    'city': city, 'ticker': ticker, 'event_ticker': event_ticker,
                                    'side': 'no', 'price': no_price,
                                    'fair': fair_cents_no, 'raw_edge': raw_edge,
                                    'adjusted_edge': adjusted_edge, 'confidence': confidence,
                                    'volume': vol, 'forecast': forecast_temp,
                                    'ensemble_details': ensemble_details,
                                    'floor': floor_s, 'cap': cap_s,
                                    'target_date': target_date.strftime("%Y-%m-%d"),  # TICK-014
                                })
                        else:
                            _bt(ticker, city, forecast_temp, ensemble_details, confidence,
                                fair_cents_no, yes_ask, yes_bid, floor_s, cap_s,
                                raw_edge, adjusted_edge, "no", no_price, "skip", f"edge_low={adjusted_edge:.1f}",
                                days_ahead=days_ahead, std_dev_used=city_std, provider_spread=provider_spread,
                                model_fair=100-model_fair_cents, market_price=no_price, blended_fair=fair_cents_no,
                                strike_type=strike_type)  # FIX-020
        except Exception as e:
            log(f"Error scanning {city}: {e}")

    opportunities.sort(key=lambda x: x['adjusted_edge'], reverse=True)
    return opportunities

# ── Trade execution ─────────────────────────────────────────────────────
def execute_trades(opportunities, state):
    global _last_circuit_breaker_alert
    
    # SAFETY: Triple-check paper mode before executing ANY trades
    if not PAPER_TRADING:
        log("=" * 70)
        log("⚠️  WARNING: LIVE TRADING MODE DETECTED!")
        log("⚠️  REAL money will be used for these trades!")
        log("⚠️  Real API orders WILL be placed on Kalshi!")
        log("=" * 70)
        
        # In daemon mode, we can't prompt for input, but we log extensively
        # If you want to enable live trading, you must:
        # 1. Set PAPER_TRADING = False at the top of this file
        # 2. Run the pre-flight checklist script first
        # 3. Monitor carefully for the first hour
        log("LIVE TRADING IS ACTIVE - proceeding with real trades")
    else:
        log("✓ Paper trading mode: Simulating trades (no real API calls)")
    
    # TICK-014: Circuit breaker check (skip for paper trading)
    if not PAPER_TRADING:
        pnl_data = load_pnl()
        can_trade, reason = check_circuit_breaker(pnl_data, state)
        if not can_trade:
            log(f"CIRCUIT BREAKER: {reason} — stopping trades")
            
            # Only send Telegram alert once per hour
            now = time.time()
            if now - _last_circuit_breaker_alert >= CIRCUIT_BREAKER_ALERT_INTERVAL:
                tg_notify_system_alert({
                    'level': 'critical',
                    'title': 'Circuit Breaker Activated',
                    'message': f'{reason}\nTrading paused for the period.',
                })
                _last_circuit_breaker_alert = now
            
            return 0
    
    today = datetime.now().strftime("%Y-%m-%d")
    if state.get('last_trade_date') != today:
        state['daily_trades'] = 0
        state['last_trade_date'] = today

    balance = get_balance()
    event_positions, open_tickers = get_positions()
    open_count = len([p for p in event_positions if p.get('event_exposure', 0) > 0])

    state_tickers = {p['ticker'] for p in state.get('positions', []) if p.get('ticker')}
    all_held = open_tickers | state_tickers

    log(f"Balance: ${balance/100:.2f} | Positions: {open_count} | Daily: {state['daily_trades']} | Held events: {len(all_held)}")

    # TICK-014: Track city+date combos already traded this cycle
    city_date_traded = set()
    # Also include city_date from existing state positions
    for p in state.get('positions', []):
        # Support both old city_date format and new target_date format
        cd = p.get('city_date')
        if cd:
            city_date_traded.add(cd)
        # Also construct from city+target_date if available
        city = p.get('city')
        target_date = p.get('target_date')
        if city and target_date:
            city_date_traded.add(f"{city}_{target_date}")

    trades_made = 0
    for opp in opportunities:
        if state['daily_trades'] >= MAX_DAILY_TRADES:
            log("Daily trade limit reached"); break
        if open_count + trades_made >= MAX_OPEN_POSITIONS:
            log("Max positions reached"); break

        # Position dedup: check ticker, event_ticker, and prefix match
        opp_ticker = opp['ticker']
        opp_event = opp.get('event_ticker', '')
        already_in = (opp_ticker in all_held
                      or opp_event in open_tickers
                      or any(opp_ticker.startswith(et) for et in open_tickers))
        if already_in:
            log(f"  SKIP {opp_ticker} — already positioned")
            continue

        # TICK-014: Correlation group limit check
        opp_city = opp['city']
        group = get_correlation_group(opp_city)
        group_count = sum(1 for p in state['positions'] if get_correlation_group(p.get('city', '')) == group)
        if group_count >= MAX_PER_GROUP:
            log(f"  SKIP {opp_ticker} — correlation group '{group}' at limit ({group_count}/{MAX_PER_GROUP})")
            continue

        # TICK-014: Per-city-per-date dedup (replaces TICK-012 approach)
        opp_target_date = opp.get('target_date')
        if opp_target_date:
            opp_city_date = (opp_city, opp_target_date)
            existing_city_dates = {(p.get('city'), p.get('target_date')) for p in state['positions']}
            if opp_city_date in existing_city_dates:
                log(f"  SKIP {opp_ticker} — already positioned in {opp_city} for {opp_target_date}")
                continue
            # Also check if already traded this city+date in current cycle
            city_date_key = f"{opp_city}_{opp_target_date}"
            if city_date_key in city_date_traded:
                log(f"  SKIP {opp_ticker} — already traded {opp_city} for {opp_target_date} this cycle")
                continue

        price = opp['price']
        
        # TICK-013: Kelly criterion position sizing
        # FIX-008: opp['fair'] is already the correct fair value for the side we're betting
        fair_p = opp['fair'] / 100.0
        count = kelly_size(fair_p, price, balance, fraction=0.25)
        
        if count < 1:
            log(f"  SKIP {opp_ticker} — Kelly says 0 contracts (fair_p={fair_p:.3f}, price={price}c)")
            continue
        
        # FIX-014: Enforce MAX_COST_PER_TRADE cap
        original_count = count
        cost_cents = count * price
        if cost_cents > MAX_COST_PER_TRADE:
            count = MAX_COST_PER_TRADE // price
            if count < 1:
                log(f"  SKIP {opp_ticker} — Cost cap would reduce to 0 contracts (Kelly wanted {original_count}, price={price}c)")
                continue
            log(f"  Capped {opp_ticker} from {original_count} to {count} contracts (cost limit: ${MAX_COST_PER_TRADE/100:.2f})")
        
        total_cost = count * price
        if total_cost > balance - 500:
            continue

        # Description
        if opp['cap'] and not opp.get('floor'):
            desc = f"{opp['city']} below {opp['cap']}°F"
        elif opp.get('floor') and not opp.get('cap'):
            desc = f"{opp['city']} above {opp['floor']}°F"
        else:
            desc = f"{opp['city']} {opp.get('floor')}-{opp.get('cap')}°F"

        log(f"TRADE: {opp['side'].upper()} {count}x {opp_ticker} @ {price}c | fair={opp['fair']}c adj_edge={opp['adjusted_edge']:.1f}c conf={opp['confidence']:.2f} | {desc} (fcst:{opp['forecast']}°F)")

        if PAPER_TRADING:
            # Paper trading mode: log trade without executing
            log(f"  → PAPER TRADE: Would buy {count}x {opp_ticker} {opp['side']} @ {price}¢ (cost=${total_cost/100:.2f})")
            
            # Log paper trade to JSONL
            paper_trade = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "ticker": opp_ticker,
                "side": opp['side'],
                "price": price,
                "count": count,
                "cost": total_cost,
                "forecast": opp['forecast'],
                "fair_cents": opp['fair'],
                "edge": opp['adjusted_edge'],
                "confidence": opp['confidence'],
                "reason": "paper_trade",
                "settlement_date": opp.get('target_date'),
                "city": opp['city'],
                "status": "open",
                "description": desc
            }
            log_paper_trade(paper_trade)
            
            prov_count = opp['ensemble_details'].get('provider_count', 0)
            tg_notify_trade_opened({
                'ticker': opp_ticker,
                'side': opp['side'],
                'count': count,
                'price': price,
                'description': desc,
                'forecast': opp['forecast'],
                'provider_count': prov_count,
                'confidence': opp['confidence'],
                'edge': opp['adjusted_edge'],
                'cost': total_cost,
                'is_paper': True
            })
            
            state['daily_trades'] += 1
            trades_made += 1
            state['positions'].append({
                'ticker': opp_ticker, 'side': opp['side'], 'count': count,
                'price': price, 'fair': opp['fair'],
                'raw_edge': opp['raw_edge'], 'adjusted_edge': opp['adjusted_edge'],
                'confidence': opp['confidence'], 'city': opp['city'],
                'forecast': opp['forecast'],
                'ensemble_details': opp['ensemble_details'],
                'fair_cents': opp.get('fair_cents'),
                'trade_time': datetime.now(timezone.utc).isoformat(),
                'city_date': city_date_key,  # TICK-012
                'target_date': opp.get('target_date'),  # TICK-014
                'paper_trade': True,  # Mark as paper trade
            })
            city_date_traded.add(city_date_key)  # TICK-012
            balance -= total_cost  # Track simulated balance
            all_held.add(opp_ticker)
        else:
            # Live trading: execute real order
            try:
                result = place_order(opp_ticker, opp['side'], count, price)
                if 'order' in result:
                    order = result['order']
                    log(f"  → Order {order.get('order_id','?')}: {order.get('status','?')} filled={order.get('filled_count',0)}")
                    prov_count = opp['ensemble_details'].get('provider_count', 0)
                    tg_notify_trade_opened({
                        'ticker': opp_ticker,
                        'side': opp['side'],
                        'count': count,
                        'price': price,
                        'description': desc,
                        'forecast': opp['forecast'],
                        'provider_count': prov_count,
                        'confidence': opp['confidence'],
                        'edge': opp['adjusted_edge'],
                        'cost': total_cost,
                        'is_paper': False
                    })
                    state['daily_trades'] += 1
                    trades_made += 1
                    state['positions'].append({
                        'ticker': opp_ticker, 'side': opp['side'], 'count': count,
                        'price': price, 'fair': opp['fair'],
                        'raw_edge': opp['raw_edge'], 'adjusted_edge': opp['adjusted_edge'],
                        'confidence': opp['confidence'], 'city': opp['city'],
                        'forecast': opp['forecast'],
                        'ensemble_details': opp['ensemble_details'],
                        'fair_cents': opp.get('fair_cents'),
                        'trade_time': datetime.now(timezone.utc).isoformat(),
                        'city_date': city_date_key,  # TICK-012
                        'target_date': opp.get('target_date'),  # TICK-014
                    })
                    city_date_traded.add(city_date_key)  # TICK-012
                    balance -= total_cost
                    all_held.add(opp_ticker)
                else:
                    log(f"  → Order failed: {json.dumps(result)[:200]}")
            except Exception as e:
                log(f"  → Order error: {e}")

    return trades_made

# ── NOAA Observations API for actual temperature ────────────────────────
def fetch_actual_high_temp(city, target_date):
    """Fetch actual observed high temperature from NOAA observations API.
    
    Args:
        city: City code (e.g., 'PHX')
        target_date: datetime object for the target date
        
    Returns:
        Float temperature in °F, or None on error
    """
    try:
        city_cfg = CITY_CONFIGS.get(city)
        if not city_cfg or 'station' not in city_cfg:
            log(f"  No station configured for {city}")
            return None
            
        station = city_cfg['station']
        # NOAA observations API uses ISO8601 format
        date_str = target_date.strftime("%Y-%m-%d")
        start_time = f"{date_str}T00:00:00Z"
        end_time = f"{date_str}T23:59:59Z"
        
        url = f"https://api.weather.gov/stations/{station}/observations"
        params = {'start': start_time, 'end': end_time}
        headers = {'User-Agent': 'KaelWeatherBot/2.0'}
        
        r = requests.get(url, params=params, headers=headers, timeout=15)
        r.raise_for_status()
        
        observations = r.json().get('features', [])
        if not observations:
            log(f"  No observations found for {station} on {date_str}")
            return None
        
        # Extract all temperature observations and find the max
        temps_f = []
        for obs in observations:
            props = obs.get('properties', {})
            temp_c = props.get('temperature', {}).get('value')
            if temp_c is not None:
                temp_f = (temp_c * 9/5) + 32
                temps_f.append(temp_f)
        
        if temps_f:
            actual_high = max(temps_f)
            log(f"  Actual high for {city} on {date_str}: {actual_high:.1f}°F")
            return actual_high
        else:
            log(f"  No valid temperature observations for {station} on {date_str}")
            return None
            
    except Exception as e:
        log(f"  Error fetching actual temp for {city}: {e}")
        return None


# ── Settlement check ────────────────────────────────────────────────────
def check_settled(state):
    new_positions = []
    for pos in state.get('positions', []):
        try:
            # For paper trades, check if market is settled via API but don't require real position
            is_paper = pos.get('paper_trade', False)
            
            data = kalshi_request('GET', f'/trade-api/v2/markets/{pos["ticker"]}')
            m = data.get('market', {})
            result = m.get('result')
            if result:
                won = (result == pos['side'])
                pnl = (100 - pos['price']) * pos['count'] if won else -(pos['price'] * pos['count'])
                state['total_pnl_cents'] = state.get('total_pnl_cents', 0) + pnl
                
                # TICK-012: Fetch actual observed temperature for feedback loop
                # TICK-015 Fix 6: Corrected ticker date parsing
                # FIX-011: Date parsing validation
                city = pos.get('city')
                actual_temp = None
                if city and 'ticker' in pos:
                    try:
                        # Parse settlement date from ticker format: KXHIGHTATL-26FEB14-T61
                        ticker = pos['ticker']
                        import re
                        date_match = re.search(r'-(\d{2})(JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)(\d{2})-', ticker, re.IGNORECASE)
                        
                        # FIX-011: Validate regex match exists
                        if not date_match:
                            log(f"  Could not parse date from ticker: {ticker}")
                            raise ValueError(f"No date match in ticker: {ticker}")
                        
                        # FIX-011: Extract and validate month
                        month_str = date_match.group(2).lower()[:3]
                        month = MONTH_MAP.get(month_str)
                        if month is None:
                            log(f"  ERROR: Unknown month '{month_str}' in ticker: {ticker}")
                            raise ValueError(f"Unknown month: {month_str}")
                        
                        # FIX-011: Parse year and day
                        yy = int(date_match.group(1))
                        dd = int(date_match.group(3))
                        year = 2000 + yy
                        
                        # FIX-011: Wrap datetime() in try/except with validation
                        try:
                            settlement_date = datetime(year, month, dd)
                        except ValueError as e:
                            log(f"  ERROR: Invalid date {year}-{month:02d}-{dd} from ticker {ticker}: {e}")
                            raise
                        
                        if date_match:
                            actual_temp = fetch_actual_high_temp(city, settlement_date)
                            
                            # Record accuracy for each provider in the ensemble
                            if actual_temp is not None:
                                ensemble_details = pos.get('ensemble_details', {})
                                individual_forecasts = ensemble_details.get('individual_forecasts', {})
                                for provider_name, forecast_value in individual_forecasts.items():
                                    weather_ensemble.record_accuracy(provider_name, forecast_value, actual_temp)
                                    log(f"  Recorded {provider_name} accuracy: predicted={forecast_value:.1f}°F actual={actual_temp:.1f}°F")
                        else:
                            log(f"  Could not parse settlement date from ticker: {ticker}")
                    except Exception as e:
                        log(f"  Error in feedback loop for {ticker}: {e}")
                
                # Log settlement to JSONL for analytics
                settlement_entry = {
                    "ts": datetime.now(timezone.utc).isoformat(),
                    "ticker": pos["ticker"],
                    "city": pos.get("city"),
                    "side": pos["side"],
                    "count": pos["count"],
                    "price_cents": pos["price"],
                    "cost_cents": pos["price"] * pos["count"],
                    "result": result,
                    "won": won,
                    "pnl_cents": pnl,
                    "forecast": pos.get("forecast"),
                    "fair_cents": pos.get("fair_cents"),
                    "raw_edge": pos.get("raw_edge"),
                    "adjusted_edge": pos.get("adjusted_edge"),
                    "confidence": pos.get("confidence"),
                    "ensemble_details": pos.get("ensemble_details"),
                    "trade_time": pos.get("trade_time"),
                    "actual_temp": actual_temp,  # TICK-012: Add actual observed temperature
                    "paper_trade": is_paper  # Mark if paper trade
                }
                with open(SETTLEMENT_LOG_PATH, 'a') as f:
                    f.write(json.dumps(settlement_entry) + '\n')
                
                # Update paper trades log with settlement result
                if is_paper:
                    paper_settlement = {
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "ticker": pos["ticker"],
                        "side": pos["side"],
                        "price": pos["price"],
                        "count": pos["count"],
                        "cost": pos["price"] * pos["count"],
                        "forecast": pos.get("forecast"),
                        "fair_cents": pos.get("fair_cents"),
                        "edge": pos.get("adjusted_edge"),
                        "confidence": pos.get("confidence"),
                        "reason": "settlement",
                        "settlement_date": pos.get("target_date"),
                        "city": pos.get("city"),
                        "status": "settled",
                        "result": result,
                        "won": won,
                        "pnl_cents": pnl,
                        "actual_temp": actual_temp
                    }
                    log_paper_trade(paper_settlement)
                
                emoji = "✅" if won else "❌"
                actual_str = f" | Actual: {actual_temp:.1f}°F" if actual_temp else ""
                trade_type = "PAPER " if is_paper else ""
                log(f"{trade_type}SETTLED: {pos['ticker']} → {'WIN' if won else 'LOSS'} ${pnl/100:.2f} (total: ${state['total_pnl_cents']/100:.2f}){actual_str}")
                
                tg_notify_settlement({
                    'ticker': pos['ticker'],
                    'won': won,
                    'pnl_cents': pnl,
                    'total_pnl_cents': state['total_pnl_cents'],
                    'actual_temp': actual_temp,
                    'forecast': pos.get('forecast'),
                    'is_paper': is_paper
                })
                
                record_pnl(pnl, pos['ticker'])
            else:
                new_positions.append(pos)
        except Exception as e:
            # FIX-016: Log settlement check errors, keep position for retry
            log(f"ERROR in check_settled for {pos.get('ticker', '?')}: {e}")
            new_positions.append(pos)
    state['positions'] = new_positions

# ── Main Loop ───────────────────────────────────────────────────────────
def main():
    log("=" * 70)
    if PAPER_TRADING:
        log("🧪 PAPER TRADING MODE ACTIVE — No real money at risk")
    log(f"Unified Kalshi Weather Daemon — {len(SERIES)} cities")
    log(f"Providers: {len(weather_ensemble.providers)} | Min edge: {MIN_EDGE_CENTS}c | Min confidence: {MIN_CONFIDENCE_SCORE}")
    log(f"Cities: {', '.join(sorted(SERIES.keys()))}")

    state = load_state()
    
    # Always fetch and store real balance (even in paper mode)
    real_balance = get_real_balance()
    state['balance'] = real_balance
    
    if PAPER_TRADING:
        # For paper trading, use a simulated balance (we'll track it in state)
        if 'paper_balance' not in state:
            state['paper_balance'] = 10000  # Default $100 paper money
        balance = state['paper_balance']
        log(f"Paper trading balance: ${balance/100:.2f} | Real account: ${real_balance/100:.2f}")
    else:
        balance = real_balance
        log(f"Account balance: ${balance/100:.2f}")

    while True:
        try:
            check_settled(state)
            save_state(state)

            opps = find_opportunities()
            if opps:
                log(f"Found {len(opps)} opportunities (best: {opps[0]['city']} adj_edge={opps[0]['adjusted_edge']:.1f}c)")
                for i, o in enumerate(opps[:5]):
                    log(f"  #{i+1}: {o['side']} {o['ticker']} adj_edge={o['adjusted_edge']:.1f}c conf={o['confidence']:.2f}")
                execute_trades(opps, state)
            else:
                log("No opportunities above threshold")

            save_state(state)
        except Exception as e:
            log(f"ERROR in main loop: {e}")
            traceback.print_exc()

        # Update paper balance in state
        if PAPER_TRADING:
            state['paper_balance'] = balance
            save_state(state)
        
        interval = get_poll_interval()
        log(f"Sleeping {interval//60} min (smart poll)...")
        time.sleep(interval)

if __name__ == '__main__':
    main()
