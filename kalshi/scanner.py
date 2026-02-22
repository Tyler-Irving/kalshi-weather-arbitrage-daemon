"""
Opportunity scanner.

Iterates over every city / event / market on Kalshi, fetches ensemble
forecasts, computes edges, applies filters, and returns a ranked list
of trading opportunities.
"""
import json
from datetime import datetime

from weather_providers import CITY_CONFIGS

from kalshi.config import (
    SERIES,
    PAPER_TRADING,
    MIN_VOLUME,
    MIN_CONFIDENCE_SCORE,
    MIN_EDGE_CENTS,
    MIN_YES_PRICE,
    MIN_NO_PRICE,
    MAX_EDGE_CENTS,
    MAX_SPREAD,
    MAX_DISAGREEMENT_CENTS,
    MAX_FAIR_MARKET_RATIO,
    MODEL_WEIGHT,
    get_city_std_dev,
)
from kalshi.kalshi_api import kalshi_request
from kalshi.probability import (
    fair_probability,
    calculate_confidence_score,
    market_adjusted_fair,
    detect_contract_type,
    parse_event_date,
)
from kalshi.forecast import get_staleness_adjusted_forecast
from kalshi.logger import log, log_backtest


# ── Backtest entry builder ───────────────────────────────────────────────

def _bt(ticker, city, forecast, ensemble_details, confidence, fair_cents,
        yes_ask, yes_bid, floor_s, cap_s, raw_edge, adjusted_edge,
        side, price, action, skip_reason=None, days_ahead=None,
        std_dev_used=None, provider_spread=None, model_fair=None,
        market_price=None, blended_fair=None, strike_type=None):
    """Build and write a backtest JSONL entry."""
    log_backtest({
        "ts": datetime.now().isoformat(),
        "ticker": ticker,
        "city": city,
        "forecast": forecast,
        "ensemble_details": ensemble_details,
        "confidence": round(confidence, 4) if confidence else None,
        "fair_cents": fair_cents,
        "market_yes_ask": yes_ask,
        "market_yes_bid": yes_bid,
        "floor_strike": floor_s,
        "cap_s": cap_s,
        "strike_type": strike_type,
        "raw_edge": raw_edge,
        "adjusted_edge": round(adjusted_edge, 2) if adjusted_edge is not None else None,
        "side": side,
        "price": price,
        "action": action,
        "skip_reason": skip_reason,
        "days_ahead": days_ahead,
        "std_dev_used": round(std_dev_used, 2) if std_dev_used else None,
        "provider_spread": round(provider_spread, 2) if provider_spread is not None else None,
        "model_fair": model_fair,
        "market_price": market_price,
        "blended_fair": blended_fair,
    })


# ── YES / NO side evaluators ────────────────────────────────────────────

def _evaluate_yes_side(ticker, city, yes_ask, yes_bid, floor_s, cap_s,
                       strike_type, forecast_temp, ensemble_details,
                       confidence, fair_p, model_fair_cents, half_spread,
                       days_ahead, city_std, provider_spread, event_ticker):
    """Evaluate the YES side of a contract. Returns an opportunity dict or None."""
    if yes_ask <= 0 or yes_ask >= 95:
        return None

    if yes_ask < MIN_YES_PRICE:
        _bt(ticker, city, forecast_temp, ensemble_details, confidence,
            model_fair_cents, yes_ask, yes_bid, floor_s, cap_s,
            None, None, "yes", yes_ask, "skip", f"yes_price_floor={yes_ask}",
            days_ahead=days_ahead, std_dev_used=city_std, provider_spread=provider_spread,
            model_fair=model_fair_cents, market_price=yes_ask, strike_type=strike_type)
        return None  # fall through to NO side in caller

    # Model vs market disagreement (pre-blend)
    model_disagreement = abs(model_fair_cents - yes_ask)
    if model_disagreement > MAX_DISAGREEMENT_CENTS:
        log(f"  SKIP {ticker} YES — model disagreement {model_disagreement}\u00a2 > {MAX_DISAGREEMENT_CENTS}\u00a2")
        _bt(ticker, city, forecast_temp, ensemble_details, confidence,
            model_fair_cents, yes_ask, yes_bid, floor_s, cap_s,
            None, None, "yes", yes_ask, "skip", f"model_disagreement={model_disagreement}",
            days_ahead=days_ahead, std_dev_used=city_std, provider_spread=provider_spread,
            model_fair=model_fair_cents, market_price=yes_ask, strike_type=strike_type)
        return "skip_contract"  # sentinel: skip entire contract

    # Bayesian blend
    market_p_yes = yes_ask / 100.0
    blended_p = market_adjusted_fair(fair_p, market_p_yes, MODEL_WEIGHT)
    fair_cents = round(blended_p * 100)

    # Blended disagreement filter
    disagreement = abs(fair_cents - yes_ask)
    if disagreement > MAX_DISAGREEMENT_CENTS:
        log(f"  SKIP {ticker} YES — disagreement {disagreement}\u00a2 > {MAX_DISAGREEMENT_CENTS}\u00a2")
        _bt(ticker, city, forecast_temp, ensemble_details, confidence,
            fair_cents, yes_ask, yes_bid, floor_s, cap_s,
            None, None, "yes", yes_ask, "skip", f"disagreement={disagreement}",
            days_ahead=days_ahead, std_dev_used=city_std, provider_spread=provider_spread,
            model_fair=model_fair_cents, market_price=yes_ask, blended_fair=fair_cents,
            strike_type=strike_type)
        return "skip_contract"

    # Ratio filter
    if yes_ask > 0 and fair_cents / yes_ask > MAX_FAIR_MARKET_RATIO:
        ratio = fair_cents / yes_ask
        log(f"  SKIP {ticker} YES — ratio {ratio:.1f}x > {MAX_FAIR_MARKET_RATIO}x")
        _bt(ticker, city, forecast_temp, ensemble_details, confidence,
            fair_cents, yes_ask, yes_bid, floor_s, cap_s,
            None, None, "yes", yes_ask, "skip", f"ratio={ratio:.1f}x",
            days_ahead=days_ahead, std_dev_used=city_std, provider_spread=provider_spread,
            model_fair=model_fair_cents, market_price=yes_ask, blended_fair=fair_cents,
            strike_type=strike_type)
        return "skip_contract"

    raw_edge = fair_cents - yes_ask - half_spread
    adjusted_edge = raw_edge * confidence

    log(f"  {ticker} YES: model={model_fair_cents}\u00a2 market={yes_ask}\u00a2 blended={fair_cents}\u00a2 edge={adjusted_edge:.1f}\u00a2")

    if adjusted_edge < MIN_EDGE_CENTS:
        _bt(ticker, city, forecast_temp, ensemble_details, confidence,
            fair_cents, yes_ask, yes_bid, floor_s, cap_s,
            raw_edge, adjusted_edge, "yes", yes_ask, "skip", f"edge_low={adjusted_edge:.1f}",
            days_ahead=days_ahead, std_dev_used=city_std, provider_spread=provider_spread,
            model_fair=model_fair_cents, market_price=yes_ask, blended_fair=fair_cents,
            strike_type=strike_type)
        return None

    if adjusted_edge > MAX_EDGE_CENTS:
        log(f"  WARN {ticker} YES adj_edge={adjusted_edge:.0f}c > {MAX_EDGE_CENTS}c — likely stale, skip")
        _bt(ticker, city, forecast_temp, ensemble_details, confidence,
            fair_cents, yes_ask, yes_bid, floor_s, cap_s,
            raw_edge, adjusted_edge, "yes", yes_ask, "skip", f"edge_cap={adjusted_edge:.0f}",
            days_ahead=days_ahead, std_dev_used=city_std, provider_spread=provider_spread,
            model_fair=model_fair_cents, market_price=yes_ask, blended_fair=fair_cents,
            strike_type=strike_type)
        return None

    _bt(ticker, city, forecast_temp, ensemble_details, confidence,
        fair_cents, yes_ask, yes_bid, floor_s, cap_s,
        raw_edge, adjusted_edge, "yes", yes_ask, "trade", None,
        days_ahead=days_ahead, std_dev_used=city_std, provider_spread=provider_spread,
        model_fair=model_fair_cents, market_price=yes_ask, blended_fair=fair_cents,
        strike_type=strike_type)

    return {
        'city': city, 'ticker': ticker, 'event_ticker': event_ticker,
        'side': 'yes', 'price': yes_ask,
        'fair': fair_cents, 'model_fair': model_fair_cents,
        'raw_edge': raw_edge,
        'adjusted_edge': adjusted_edge, 'confidence': confidence,
        'volume': None,  # filled by caller
        'forecast': forecast_temp,
        'ensemble_details': ensemble_details,
        'floor': floor_s, 'cap': cap_s,
        'target_date': None,  # filled by caller
    }


def _evaluate_no_side(ticker, city, yes_ask, yes_bid, floor_s, cap_s,
                      strike_type, forecast_temp, ensemble_details,
                      confidence, fair_p, model_fair_cents, half_spread,
                      days_ahead, city_std, provider_spread, event_ticker):
    """Evaluate the NO side of a contract. Returns an opportunity dict or None."""
    if yes_bid <= 0 or yes_bid <= 5:
        return None

    no_price = 100 - yes_bid

    if no_price < MIN_NO_PRICE:
        _bt(ticker, city, forecast_temp, ensemble_details, confidence,
            model_fair_cents, yes_ask, yes_bid, floor_s, cap_s,
            None, None, "no", no_price, "skip", f"no_price_floor={no_price}",
            days_ahead=days_ahead, std_dev_used=city_std, provider_spread=provider_spread,
            model_fair=model_fair_cents, market_price=yes_bid, strike_type=strike_type)
        return None

    model_fair_no = 100 - model_fair_cents
    model_disagreement = abs(model_fair_no - no_price)
    if model_disagreement > MAX_DISAGREEMENT_CENTS:
        log(f"  SKIP {ticker} NO — model disagreement {model_disagreement}\u00a2 > {MAX_DISAGREEMENT_CENTS}\u00a2")
        _bt(ticker, city, forecast_temp, ensemble_details, confidence,
            model_fair_no, yes_ask, yes_bid, floor_s, cap_s,
            None, None, "no", no_price, "skip", f"model_disagreement={model_disagreement}",
            days_ahead=days_ahead, std_dev_used=city_std, provider_spread=provider_spread,
            model_fair=model_fair_no, market_price=no_price, strike_type=strike_type)
        return None

    # Bayesian blend — NO side uses yes_bid (not yes_ask) because buying NO
    # is equivalent to selling YES at the bid, so the bid is the relevant
    # market-implied probability for this side.
    market_p_yes = yes_bid / 100.0
    blended_p = market_adjusted_fair(fair_p, market_p_yes, MODEL_WEIGHT)
    fair_cents_yes = round(blended_p * 100)
    fair_cents_no = 100 - fair_cents_yes

    disagreement = abs(fair_cents_no - no_price)
    if disagreement > MAX_DISAGREEMENT_CENTS:
        log(f"  SKIP {ticker} NO — disagreement {disagreement}\u00a2 > {MAX_DISAGREEMENT_CENTS}\u00a2")
        _bt(ticker, city, forecast_temp, ensemble_details, confidence,
            fair_cents_no, yes_ask, yes_bid, floor_s, cap_s,
            None, None, "no", no_price, "skip", f"disagreement={disagreement}",
            days_ahead=days_ahead, std_dev_used=city_std, provider_spread=provider_spread,
            model_fair=100 - model_fair_cents, market_price=no_price, blended_fair=fair_cents_no,
            strike_type=strike_type)
        return None

    if no_price > 0 and fair_cents_no / no_price > MAX_FAIR_MARKET_RATIO:
        ratio = fair_cents_no / no_price
        log(f"  SKIP {ticker} NO — ratio {ratio:.1f}x > {MAX_FAIR_MARKET_RATIO}x")
        _bt(ticker, city, forecast_temp, ensemble_details, confidence,
            fair_cents_no, yes_ask, yes_bid, floor_s, cap_s,
            None, None, "no", no_price, "skip", f"ratio={ratio:.1f}x",
            days_ahead=days_ahead, std_dev_used=city_std, provider_spread=provider_spread,
            model_fair=100 - model_fair_cents, market_price=no_price, blended_fair=fair_cents_no,
            strike_type=strike_type)
        return None

    raw_edge = yes_bid - fair_cents_yes - half_spread
    adjusted_edge = raw_edge * confidence

    log(f"  {ticker} NO: model={100 - model_fair_cents}\u00a2 market={no_price}\u00a2 blended={fair_cents_no}\u00a2 edge={adjusted_edge:.1f}\u00a2")

    if adjusted_edge < MIN_EDGE_CENTS:
        _bt(ticker, city, forecast_temp, ensemble_details, confidence,
            fair_cents_no, yes_ask, yes_bid, floor_s, cap_s,
            raw_edge, adjusted_edge, "no", no_price, "skip", f"edge_low={adjusted_edge:.1f}",
            days_ahead=days_ahead, std_dev_used=city_std, provider_spread=provider_spread,
            model_fair=100 - model_fair_cents, market_price=no_price, blended_fair=fair_cents_no,
            strike_type=strike_type)
        return None

    if adjusted_edge > MAX_EDGE_CENTS:
        log(f"  WARN {ticker} NO adj_edge={adjusted_edge:.0f}c > {MAX_EDGE_CENTS}c — likely stale, skip")
        _bt(ticker, city, forecast_temp, ensemble_details, confidence,
            fair_cents_no, yes_ask, yes_bid, floor_s, cap_s,
            raw_edge, adjusted_edge, "no", no_price, "skip", f"edge_cap={adjusted_edge:.0f}",
            days_ahead=days_ahead, std_dev_used=city_std, provider_spread=provider_spread,
            model_fair=100 - model_fair_cents, market_price=no_price, blended_fair=fair_cents_no,
            strike_type=strike_type)
        return None

    _bt(ticker, city, forecast_temp, ensemble_details, confidence,
        fair_cents_no, yes_ask, yes_bid, floor_s, cap_s,
        raw_edge, adjusted_edge, "no", no_price, "trade", None,
        days_ahead=days_ahead, std_dev_used=city_std, provider_spread=provider_spread,
        model_fair=100 - model_fair_cents, market_price=no_price, blended_fair=fair_cents_no,
        strike_type=strike_type)

    return {
        'city': city, 'ticker': ticker, 'event_ticker': event_ticker,
        'side': 'no', 'price': no_price,
        'fair': fair_cents_no, 'model_fair': 100 - model_fair_cents,
        'raw_edge': raw_edge,
        'adjusted_edge': adjusted_edge, 'confidence': confidence,
        'volume': None,
        'forecast': forecast_temp,
        'ensemble_details': ensemble_details,
        'floor': floor_s, 'cap': cap_s,
        'target_date': None,
    }


# ── Main scanner ─────────────────────────────────────────────────────────

def find_opportunities():
    """Scan all cities and return a list of trading opportunities, best-edge first."""
    opportunities = []
    forecast_cache = {}  # (city, date_str) -> (temp, details, confidence)

    if not SERIES:
        log("ERROR: SERIES is empty — no cities configured")
        return []
    if not CITY_CONFIGS:
        log("ERROR: CITY_CONFIGS is empty — configuration missing")
        return []

    for city, series in SERIES.items():
        city_cfg = CITY_CONFIGS.get(city)
        if not city_cfg:
            log(f"ERROR: No city config for {city}, skipping")
            continue

        try:
            data = kalshi_request(
                'GET',
                f'/trade-api/v2/events?series_ticker={series}'
                f'&status=open&with_nested_markets=true&limit=5',
            )
            events = data.get('events', [])

            for event in events:
                _scan_event(city, city_cfg, event, forecast_cache, opportunities)

        except Exception as e:
            log(f"Error scanning {city}: {e}")

    opportunities.sort(key=lambda x: x['adjusted_edge'], reverse=True)
    return opportunities


def _scan_event(city, city_cfg, event, forecast_cache, opportunities):
    """Process one Kalshi event (one city/date), appending to *opportunities*."""
    title = event.get('title', '')
    target_date = parse_event_date(title)
    if target_date is None:
        return

    days_ahead = max(0, (target_date.date() - datetime.now().date()).days)
    city_std = get_city_std_dev(city, target_date)

    # ── Forecast (cached per city+date) ──────────────────────────────
    cache_key = (city, target_date.strftime("%Y-%m-%d"))
    if cache_key in forecast_cache:
        forecast_temp, ensemble_details, confidence = forecast_cache[cache_key]
    else:
        ensemble_temp, ensemble_details = get_staleness_adjusted_forecast(city_cfg, target_date, city_code=city)
        if ensemble_temp is None:
            forecast_cache[cache_key] = (None, None, None)
            return
        forecast_temp = float(ensemble_temp)
        if not ensemble_details or not isinstance(ensemble_details, dict):
            log(f"ERROR: Invalid ensemble_details for {city} on {target_date.strftime('%Y-%m-%d')}")
            forecast_cache[cache_key] = (None, None, None)
            return
        confidence = calculate_confidence_score(ensemble_details, forecast_temp, city_std)
        forecast_cache[cache_key] = (forecast_temp, ensemble_details, confidence)

    if forecast_temp is None or confidence < MIN_CONFIDENCE_SCORE:
        return
    forecast_temp = float(forecast_temp)

    # Provider-spread hard filter (>6 °F disagreement → skip)
    individual = ensemble_details.get('individual_forecasts', {})
    provider_spread = None
    if len(individual) >= 2:
        vals = list(individual.values())
        provider_spread = max(vals) - min(vals)
        if provider_spread > 6.0:
            log(f"  SKIP {city} {target_date.strftime('%Y-%m-%d')} — provider spread {provider_spread:.1f}°F > 6°F")
            return

    event_ticker = event.get('event_ticker', '')

    for m in event.get('markets', []):
        _scan_market(
            m, city, city_std, event_ticker, target_date, days_ahead,
            forecast_temp, ensemble_details, confidence, provider_spread,
            opportunities,
        )


def _scan_market(m, city, city_std, event_ticker, target_date, days_ahead,
                 forecast_temp, ensemble_details, confidence,
                 provider_spread, opportunities):
    """Evaluate a single market (contract) for both YES and NO sides."""
    yes_ask = m.get('yes_ask', 0)
    yes_bid = m.get('yes_bid', 0)
    vol = m.get('volume', 0)
    floor_s = m.get('floor_strike')
    cap_s = m.get('cap_strike')
    strike_type = m.get('strike_type')
    ticker = m['ticker']

    # Validate strikes
    for label, val in [("floor_strike", floor_s), ("cap_strike", cap_s)]:
        if val is not None and (not isinstance(val, (int, float)) or val < 0):
            log(f"ERROR: Invalid {label}={val} for {ticker}, skipping")
            return
    if floor_s is None and cap_s is None:
        log(f"ERROR: Both strikes are None for {ticker}, skipping")
        return
    if strike_type not in ('less', 'greater', 'between'):
        log(f"ERROR: Invalid strike_type={strike_type} for {ticker}, skipping")
        return

    contract_type = detect_contract_type(ticker)
    if contract_type:
        log(f"  {ticker}: type={contract_type} strike_type={strike_type} floor={floor_s} cap={cap_s}")

    if (yes_ask == 0 and yes_bid == 0) or vol < MIN_VOLUME:
        return

    # Spread check
    spread = (yes_ask - yes_bid) if (yes_ask > 0 and yes_bid > 0) else 0
    if spread > MAX_SPREAD:
        log(f"  SKIP {ticker} — spread {spread}c > {MAX_SPREAD}c (illiquid)")
        _bt(ticker, city, forecast_temp, ensemble_details, confidence,
            None, yes_ask, yes_bid, floor_s, cap_s, None, None,
            None, None, "skip", f"spread={spread}",
            days_ahead=days_ahead, std_dev_used=city_std, provider_spread=provider_spread,
            strike_type=strike_type)
        return

    # Strike proximity filter
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
            strike_type=strike_type)
        return

    # Model fair probability (before blending)
    fair_p = fair_probability(
        forecast_temp, ensemble_details, floor_s, cap_s,
        city=city, target_date=target_date, days_ahead=days_ahead,
        strike_type=strike_type,
    )
    model_fair_cents = round(fair_p * 100)
    half_spread = (yes_ask - yes_bid) / 2 if (yes_ask > 0 and yes_bid > 0) else 0

    common = dict(
        ticker=ticker, city=city, yes_ask=yes_ask, yes_bid=yes_bid,
        floor_s=floor_s, cap_s=cap_s, strike_type=strike_type,
        forecast_temp=forecast_temp, ensemble_details=ensemble_details,
        confidence=confidence, fair_p=fair_p, model_fair_cents=model_fair_cents,
        half_spread=half_spread, days_ahead=days_ahead, city_std=city_std,
        provider_spread=provider_spread, event_ticker=event_ticker,
    )

    # Evaluate YES side
    yes_result = _evaluate_yes_side(**common)
    if yes_result and yes_result != "skip_contract":
        yes_result['volume'] = vol
        yes_result['target_date'] = target_date.strftime("%Y-%m-%d")
        opportunities.append(yes_result)

    # Evaluate NO side (skip if YES evaluation said to skip the whole contract)
    if yes_result != "skip_contract":
        no_result = _evaluate_no_side(**common)
        if no_result and no_result != "skip_contract":
            no_result['volume'] = vol
            no_result['target_date'] = target_date.strftime("%Y-%m-%d")
            opportunities.append(no_result)
