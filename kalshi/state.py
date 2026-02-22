"""
Persistent state and P&L tracking.

Handles loading/saving the position state file and the P&L ledger.
"""
import json
from datetime import datetime

from kalshi.config import STATE_PATH, PNL_PATH


# ── Position state ───────────────────────────────────────────────────────

def load_state():
    if STATE_PATH.exists():
        return json.load(open(STATE_PATH))
    return {
        "positions": [],
        "daily_trades": 0,
        "last_trade_date": "",
        "total_pnl_cents": 0,
    }


def save_state(state):
    with open(STATE_PATH, 'w') as f:
        json.dump(state, f, indent=2)


# ── P&L ledger ───────────────────────────────────────────────────────────

def load_pnl():
    if PNL_PATH.exists():
        return json.load(open(PNL_PATH))
    return {"weeks": {}, "daily": {}}


def save_pnl(pnl):
    with open(PNL_PATH, 'w') as f:
        json.dump(pnl, f, indent=2)


def record_pnl(amount_cents, ticker):
    """Record a settlement result in both daily and weekly buckets."""
    pnl = load_pnl()
    today = datetime.now().strftime("%Y-%m-%d")
    week_key = datetime.now().strftime("%Y-W%U")

    for bucket_key, bucket_name in [(today, "daily"), (week_key, "weeks")]:
        if bucket_key not in pnl[bucket_name]:
            pnl[bucket_name][bucket_key] = {
                "pnl_cents": 0, "trades": 0, "wins": 0, "losses": 0,
            }
        pnl[bucket_name][bucket_key]["pnl_cents"] += amount_cents
        pnl[bucket_name][bucket_key]["trades"] += 1
        if amount_cents > 0:
            pnl[bucket_name][bucket_key]["wins"] += 1
        else:
            pnl[bucket_name][bucket_key]["losses"] += 1

    save_pnl(pnl)
