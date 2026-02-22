"""
Kalshi REST API client.

Handles RSA-signed authentication, balance checks, position queries,
and order placement.  In paper-trading mode the real API is bypassed.
"""
import json
import time
import base64

import requests
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

from kalshi.config import KALSHI_BASE, KALSHI_API_KEY_ID, KALSHI_PRIVATE_KEY_PATH, PAPER_TRADING
from kalshi.logger import log


# ── Credentials ──────────────────────────────────────────────────────────

def _load_credentials():
    """Load credentials from .env file configuration."""
    if not KALSHI_API_KEY_ID:
        raise ValueError("KALSHI_API_KEY_ID not set in .env file")
    if not KALSHI_PRIVATE_KEY_PATH:
        raise ValueError("KALSHI_PRIVATE_KEY_PATH not set in .env file")
    
    # Load private key from file
    with open(KALSHI_PRIVATE_KEY_PATH, 'rb') as key_file:
        private_key = serialization.load_pem_private_key(
            key_file.read(), password=None
        )
    
    return KALSHI_API_KEY_ID, private_key


KEY_ID, PRIVATE_KEY = _load_credentials()


# ── Signed request helper ────────────────────────────────────────────────

def kalshi_request(method, path, body=None):
    """Make an authenticated request to the Kalshi API."""
    ts = str(int(time.time() * 1000))
    msg = (ts + method.upper() + path).encode()
    sig = PRIVATE_KEY.sign(
        msg,
        padding.PSS(
            mgf=padding.MGF1(hashes.SHA256()),
            salt_length=padding.PSS.DIGEST_LENGTH,
        ),
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


# ── Balance ──────────────────────────────────────────────────────────────

def get_real_balance():
    """Fetch the real Kalshi account balance (even in paper mode)."""
    try:
        return kalshi_request('GET', '/trade-api/v2/portfolio/balance').get('balance', 0)
    except Exception as e:
        log(f"ERROR fetching real balance: {e}")
        return 0


def get_balance():
    """Return simulated balance in paper mode, real balance otherwise."""
    if PAPER_TRADING:
        from paper_trading_safety import paper_get_balance
        return paper_get_balance()
    return get_real_balance()


# ── Positions ────────────────────────────────────────────────────────────

def get_positions():
    """Return (event_positions, open_event_tickers)."""
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


# ── Order placement ──────────────────────────────────────────────────────

def place_order(ticker, side, count, price_cents):
    """Place a limit order (or simulate in paper mode)."""
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
