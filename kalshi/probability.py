"""
Probability math, confidence scoring, and position sizing.

Contains the statistical core: normal CDF, Bayesian log-odds blending,
fair-probability calculation, confidence scoring, and Kelly criterion.
"""
import math
import re
from datetime import datetime, timedelta

from kalshi.config import (
    FORECAST_STD_DEV,
    MIN_PROVIDER_COUNT,
    MODEL_WEIGHT,
    MAX_CONTRACTS,
    get_city_std_dev,
)
from kalshi.logger import log


# ── Month map for date parsing ───────────────────────────────────────────

MONTH_MAP = {
    'jan': 1, 'feb': 2, 'mar': 3, 'apr': 4, 'may': 5, 'jun': 6,
    'jul': 7, 'aug': 8, 'sep': 9, 'oct': 10, 'nov': 11, 'dec': 12,
}


# ── Core math ────────────────────────────────────────────────────────────

def normal_cdf(x):
    """Standard normal cumulative distribution function."""
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def market_adjusted_fair(model_p, market_p, model_weight=MODEL_WEIGHT):
    """Bayesian blend of model probability and market price in log-odds space.

    Returns a blended probability between 0 and 1.
    """
    market_p = max(0.02, min(0.98, market_p))
    model_p = max(0.02, min(0.98, model_p))

    def logit(p):
        return math.log(p / (1 - p))

    def inv_logit(x):
        return 1 / (1 + math.exp(-x))

    blended = model_weight * logit(model_p) + (1 - model_weight) * logit(market_p)
    return inv_logit(blended)


# ── Confidence scoring ───────────────────────────────────────────────────

def calculate_confidence_score(ensemble_details, forecast_temp, std_dev):
    """Score between 0 and 1 based on provider agreement and count."""
    if not ensemble_details or ensemble_details.get('provider_count', 0) < MIN_PROVIDER_COUNT:
        return 0.0

    individual = ensemble_details.get('individual_forecasts', {})
    if len(individual) < 2:
        return 0.7  # single-provider base confidence

    forecasts = list(individual.values())
    mean_f = sum(forecasts) / len(forecasts)
    forecast_std = math.sqrt(sum((f - mean_f) ** 2 for f in forecasts) / len(forecasts))

    agreement_score = max(0.5, 1.0 - (forecast_std / 5.0))
    provider_score = min(1.0, len(individual) / 3.0)
    raw = agreement_score * 0.7 + provider_score * 0.3
    return min(1.0, max(0.0, raw))


# ── Fair probability ─────────────────────────────────────────────────────

def fair_probability(forecast_temp, ensemble_details, floor_strike, cap_strike,
                     city=None, target_date=None, std=FORECAST_STD_DEV,
                     days_ahead=1, strike_type=None):
    """Calculate fair probability for a weather contract.

    Uses city × season std-dev, lead-time scaling, and the strike geometry
    (less / greater / between) to compute a CDF-based fair value.
    """
    if not forecast_temp:
        return 0.5

    if city and target_date:
        std = get_city_std_dev(city, target_date)

    confidence = calculate_confidence_score(ensemble_details, forecast_temp, std)

    # Lead-time decay: same-day forecasts are much more accurate
    if days_ahead == 0:
        decay = 0.5
    elif days_ahead == 1:
        decay = 0.75
    else:
        decay = 1.0 + 0.35 * (days_ahead - 1)

    confidence_mult = 1.2 - 0.2 * confidence
    adjusted_std = std * confidence_mult * decay

    if adjusted_std <= 0:
        log(f"ERROR: Invalid adjusted_std={adjusted_std:.4f}, using default 1.0")
        adjusted_std = 1.0

    if strike_type == 'less':
        return normal_cdf((cap_strike - forecast_temp) / adjusted_std)
    elif strike_type == 'greater':
        return 1.0 - normal_cdf((floor_strike - forecast_temp) / adjusted_std)
    elif strike_type == 'between':
        z1 = (floor_strike - forecast_temp) / adjusted_std
        z2 = (cap_strike - forecast_temp) / adjusted_std
        return normal_cdf(z2) - normal_cdf(z1)
    else:
        log(f"ERROR: Unknown strike_type={strike_type}, returning 0.5")
        return 0.5


# ── Kelly criterion ──────────────────────────────────────────────────────

def kelly_size(fair_p, market_price_cents, bankroll_cents, fraction=0.25):
    """Quarter-Kelly position sizing for binary contracts.

    Returns the number of contracts to buy (capped by MAX_CONTRACTS).
    """
    if fair_p <= 0 or fair_p >= 1 or market_price_cents <= 0:
        return 0

    cost = market_price_cents
    payout = 100 - cost
    b = payout / cost
    q = 1 - fair_p

    f_star = (fair_p * b - q) / b
    f_safe = max(0, f_star * fraction)

    max_contracts = int((bankroll_cents * f_safe) / cost)
    return max(0, min(max_contracts, MAX_CONTRACTS))


# ── Contract helpers ─────────────────────────────────────────────────────

def detect_contract_type(ticker):
    """Detect if a contract is threshold (T) or bracket (B)."""
    if '-T' in ticker:
        return 'threshold'
    elif '-B' in ticker:
        return 'bracket'
    return None


def parse_event_date(title):
    """Parse target date from an event title string. Returns datetime or None."""
    title_lower = title.lower()
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
