"""
Trade execution and risk management.

Takes a ranked list of opportunities and executes trades subject to
position limits, correlation-group caps, circuit breakers, and
Kelly-criterion sizing.
"""
import json
import time
from datetime import datetime, timezone

from kalshi.config import (
    PAPER_TRADING,
    MAX_DAILY_TRADES,
    MAX_OPEN_POSITIONS,
    MAX_COST_PER_TRADE,
    MAX_CONTRACTS,
    MAX_PER_GROUP,
    MAX_DAILY_LOSS_CENTS,
    MAX_WEEKLY_LOSS_CENTS,
    CIRCUIT_BREAKER_ALERT_INTERVAL,
    get_correlation_group,
)
from kalshi.kalshi_api import get_balance, get_positions, place_order
from kalshi.probability import kelly_size
from kalshi.state import load_pnl
from kalshi.logger import log, log_paper_trade
from kalshi.notifications import notify_trade_opened, notify_system_alert

# Module-level throttle for circuit-breaker Telegram alerts
_last_circuit_breaker_alert = 0


# ── Circuit breaker ──────────────────────────────────────────────────────

def check_circuit_breaker(pnl_data, state):
    """Return (can_trade: bool, reason: str) based on daily/weekly loss limits.

    Includes worst-case unrealised exposure from today's open positions.
    """
    today = datetime.now().strftime("%Y-%m-%d")
    today_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    week_key = datetime.now().strftime("%Y-W%U")

    daily = pnl_data.get('daily', {}).get(today, {})
    weekly = pnl_data.get('weeks', {}).get(week_key, {})

    daily_pnl = daily.get('pnl_cents', 0)
    weekly_pnl = weekly.get('pnl_cents', 0)

    today_exposure = sum(
        p.get('count', 0) * p.get('price', 0)
        for p in state.get('positions', [])
        if p.get('trade_time', '').startswith(today)
        or p.get('trade_time', '').startswith(today_utc)
    )

    effective_daily = daily_pnl - today_exposure

    if effective_daily <= -MAX_DAILY_LOSS_CENTS:
        return False, f"Daily loss limit (incl. ${today_exposure/100:.2f} at-risk): ${effective_daily/100:.2f}"
    if weekly_pnl - today_exposure <= -MAX_WEEKLY_LOSS_CENTS:
        return False, f"Weekly loss limit (incl. exposure): ${(weekly_pnl - today_exposure)/100:.2f}"
    return True, ""


# ── Trade executor ───────────────────────────────────────────────────────

def execute_trades(opportunities, state):
    """Execute (or paper-log) the best opportunities, respecting all risk limits.

    Returns the number of trades placed.
    """
    global _last_circuit_breaker_alert

    if not PAPER_TRADING:
        log("=" * 70)
        log("WARNING: LIVE TRADING MODE — real money at risk!")
        log("=" * 70)
    else:
        log("Paper trading mode: simulating trades (no real API calls)")

    # Circuit breaker runs in both modes so paper results mirror live behaviour
    pnl_data = load_pnl()
    can_trade, reason = check_circuit_breaker(pnl_data, state)
    if not can_trade:
        mode = "PAPER " if PAPER_TRADING else ""
        log(f"{mode}CIRCUIT BREAKER: {reason} — stopping trades")
        now = time.time()
        if now - _last_circuit_breaker_alert >= CIRCUIT_BREAKER_ALERT_INTERVAL:
            notify_system_alert({
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

    log(f"Balance: ${balance/100:.2f} | Positions: {open_count} | Daily: {state['daily_trades']} | Held: {len(all_held)}")

    # Existing city+date combos
    city_date_traded = set()
    for p in state.get('positions', []):
        cd = p.get('city_date')
        if cd:
            city_date_traded.add(cd)
        c, td = p.get('city'), p.get('target_date')
        if c and td:
            city_date_traded.add(f"{c}_{td}")

    trades_made = 0
    for opp in opportunities:
        if state['daily_trades'] >= MAX_DAILY_TRADES:
            log("Daily trade limit reached")
            break
        if open_count + trades_made >= MAX_OPEN_POSITIONS:
            log("Max positions reached")
            break

        opp_ticker = opp['ticker']
        opp_event = opp.get('event_ticker', '')

        # Position dedup
        if (opp_ticker in all_held
                or opp_event in open_tickers
                or any(opp_ticker.startswith(et) for et in open_tickers)):
            log(f"  SKIP {opp_ticker} — already positioned")
            continue

        # Correlation-group cap
        opp_city = opp['city']
        group = get_correlation_group(opp_city)
        group_count = sum(
            1 for p in state['positions']
            if get_correlation_group(p.get('city', '')) == group
        )
        if group_count >= MAX_PER_GROUP:
            log(f"  SKIP {opp_ticker} — group '{group}' at limit ({group_count}/{MAX_PER_GROUP})")
            continue

        # Per-city-per-date dedup
        opp_target_date = opp.get('target_date')
        city_date_key = f"{opp_city}_{opp_target_date}" if opp_target_date else ""
        if opp_target_date:
            existing = {(p.get('city'), p.get('target_date')) for p in state['positions']}
            if (opp_city, opp_target_date) in existing:
                log(f"  SKIP {opp_ticker} — already positioned in {opp_city} for {opp_target_date}")
                continue
            if city_date_key in city_date_traded:
                log(f"  SKIP {opp_ticker} — already traded {opp_city} for {opp_target_date} this cycle")
                continue

        # Kelly sizing — use the model's own fair value (not the market-blended
        # one) so that position size reflects the model's actual conviction.
        price = opp['price']
        kelly_fair = opp.get('model_fair', opp['fair'])
        fair_p = kelly_fair / 100.0
        count = kelly_size(fair_p, price, balance, fraction=0.25)
        if count < 1:
            log(f"  SKIP {opp_ticker} — Kelly says 0 contracts")
            continue

        # Cost cap
        cost_cents = count * price
        if cost_cents > MAX_COST_PER_TRADE:
            count = MAX_COST_PER_TRADE // price
            if count < 1:
                log(f"  SKIP {opp_ticker} — cost cap reduces to 0 contracts")
                continue

        total_cost = count * price
        if total_cost > balance - 500:
            continue

        desc = _describe_contract(opp)
        log(f"TRADE: {opp['side'].upper()} {count}x {opp_ticker} @ {price}c"
            f" | fair={opp['fair']}c edge={opp['adjusted_edge']:.1f}c"
            f" conf={opp['confidence']:.2f} | {desc} (fcst:{opp['forecast']}°F)")

        position_record = {
            'ticker': opp_ticker, 'side': opp['side'], 'count': count,
            'price': price, 'fair': opp['fair'],
            'raw_edge': opp['raw_edge'], 'adjusted_edge': opp['adjusted_edge'],
            'confidence': opp['confidence'], 'city': opp['city'],
            'forecast': opp['forecast'],
            'ensemble_details': opp['ensemble_details'],
            'fair_cents': opp.get('fair_cents'),
            'trade_time': datetime.now(timezone.utc).isoformat(),
            'city_date': city_date_key,
            'target_date': opp.get('target_date'),
        }

        if PAPER_TRADING:
            trades_made += _execute_paper(
                opp, count, price, total_cost, desc, position_record,
                state, city_date_traded, all_held, city_date_key,
            )
            balance -= total_cost
        else:
            trades_made += _execute_live(
                opp, count, price, total_cost, desc, position_record,
                state, city_date_traded, all_held, city_date_key,
            )
            balance -= total_cost

    return trades_made


# ── Internal helpers ─────────────────────────────────────────────────────

def _describe_contract(opp):
    has_floor = opp.get('floor') is not None
    has_cap = opp.get('cap') is not None
    if has_cap and not has_floor:
        return f"{opp['city']} below {opp['cap']}°F"
    if has_floor and not has_cap:
        return f"{opp['city']} above {opp['floor']}°F"
    return f"{opp['city']} {opp.get('floor')}-{opp.get('cap')}°F"


def _execute_paper(opp, count, price, total_cost, desc, position_record,
                   state, city_date_traded, all_held, city_date_key):
    opp_ticker = opp['ticker']
    log(f"  PAPER TRADE: Would buy {count}x {opp_ticker} {opp['side']} @ {price}\u00a2 (cost=${total_cost/100:.2f})")

    log_paper_trade({
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
        "description": desc,
    })

    prov_count = opp['ensemble_details'].get('provider_count', 0)
    notify_trade_opened({
        'ticker': opp_ticker, 'side': opp['side'], 'count': count,
        'price': price, 'description': desc, 'forecast': opp['forecast'],
        'provider_count': prov_count, 'confidence': opp['confidence'],
        'edge': opp['adjusted_edge'], 'cost': total_cost, 'is_paper': True,
    })

    position_record['paper_trade'] = True
    state['daily_trades'] += 1
    state['positions'].append(position_record)
    city_date_traded.add(city_date_key)
    all_held.add(opp_ticker)
    return 1


def _execute_live(opp, count, price, total_cost, desc, position_record,
                  state, city_date_traded, all_held, city_date_key):
    opp_ticker = opp['ticker']
    try:
        result = place_order(opp_ticker, opp['side'], count, price)
        if 'order' in result:
            order = result['order']
            log(f"  Order {order.get('order_id','?')}: {order.get('status','?')} filled={order.get('filled_count',0)}")
            prov_count = opp['ensemble_details'].get('provider_count', 0)
            notify_trade_opened({
                'ticker': opp_ticker, 'side': opp['side'], 'count': count,
                'price': price, 'description': desc, 'forecast': opp['forecast'],
                'provider_count': prov_count, 'confidence': opp['confidence'],
                'edge': opp['adjusted_edge'], 'cost': total_cost, 'is_paper': False,
            })
            state['daily_trades'] += 1
            state['positions'].append(position_record)
            city_date_traded.add(city_date_key)
            all_held.add(opp_ticker)
            return 1
        else:
            log(f"  Order failed: {json.dumps(result)[:200]}")
    except Exception as e:
        log(f"  Order error: {e}")
    return 0
