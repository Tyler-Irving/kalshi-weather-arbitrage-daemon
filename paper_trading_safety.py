#!/usr/bin/env python3
"""
Paper Trading Safety Module

Provides safety wrappers for Kalshi trading functions to prevent accidental live trading.
"""
import json
from pathlib import Path
from datetime import datetime, timezone

PAPER_TRADES_PATH = Path(__file__).parent / "paper_trades.jsonl"

def log_paper_trade(action, **kwargs):
    """Log a paper trade to JSONL file for later analysis."""
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "action": action,
        **kwargs
    }
    with open(PAPER_TRADES_PATH, 'a') as f:
        f.write(json.dumps(entry, default=str) + '\n')

def paper_place_order(ticker, side, count, price_cents):
    """
    Simulates placing an order without calling Kalshi API.
    
    Returns a mock successful response matching Kalshi's order format.
    """
    mock_order_id = f"PAPER-{ticker}-{datetime.now().strftime('%Y%m%d%H%M%S')}"
    
    # Log the paper trade
    log_paper_trade(
        action="place_order",
        ticker=ticker,
        side=side,
        count=count,
        price_cents=price_cents,
        cost_cents=count * price_cents,
        order_id=mock_order_id
    )
    
    # Return mock response matching Kalshi format
    return {
        "order": {
            "order_id": mock_order_id,
            "status": "filled",  # Simulate immediate fill for paper trading
            "ticker": ticker,
            "action": "buy",
            "side": side,
            "count": count,
            "filled_count": count,
            "yes_price": price_cents if side == 'yes' else None,
            "no_price": price_cents if side == 'no' else None,
            "placed_at": datetime.now(timezone.utc).isoformat(),
            "is_paper_trade": True  # Flag to identify paper trades
        }
    }

def paper_get_balance():
    """
    Returns a simulated balance for paper trading.
    
    Default: $1000 (100,000 cents)
    """
    # TODO: Track paper balance based on paper trades
    return 100000  # $1000 starting balance

def paper_get_positions():
    """
    Returns empty positions for paper trading.
    
    In paper mode, we track positions in state.json, not via API.
    """
    return [], set()  # (event_positions, open_tickers)

def safe_execute_trades_wrapper(execute_func):
    """
    Decorator to add safety checks to execute_trades function.
    
    Usage:
        execute_trades = safe_execute_trades_wrapper(execute_trades)
    """
    def wrapper(opportunities, state):
        from kalshi_unified import PAPER_TRADING, log
        
        # SAFETY: Triple-check paper mode
        if not PAPER_TRADING:
            log("⚠️  WARNING: LIVE TRADING MODE DETECTED!")
            log("⚠️  Real money will be at risk!")
            log("⚠️  Press Ctrl+C now to abort if this was not intentional.")
            
            # In production, this would require user confirmation
            # For daemon mode, we'll just log extensively
            log("=" * 70)
            log("LIVE TRADING ENABLED - EXECUTING REAL TRADES")
            log("=" * 70)
        else:
            log("✓ Paper trading mode active - no real API calls will be made")
        
        return execute_func(opportunities, state)
    
    return wrapper
