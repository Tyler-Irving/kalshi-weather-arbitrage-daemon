#!/usr/bin/env python3
"""
Kalshi Weather Arbitrage Daemon — main entry point.

Runs an infinite loop that:
  1. Checks for settled positions and records P&L.
  2. Scans 11 cities for mispriced weather contracts.
  3. Executes trades (or paper-logs them) within risk limits.
  4. Sleeps on a smart polling interval aligned to model-update times.

All heavy lifting lives in the `kalshi/` package — this file is just
the orchestrator.
"""
import time
import traceback

from kalshi.config import PAPER_TRADING, SERIES, MIN_EDGE_CENTS, MIN_CONFIDENCE_SCORE
from kalshi.kalshi_api import get_real_balance
from kalshi.forecast import weather_ensemble, get_poll_interval
from kalshi.scanner import find_opportunities
from kalshi.execution import execute_trades
from kalshi.settlement import check_settled
from kalshi.state import load_state, save_state
from kalshi.logger import log


def main():
    log("=" * 70)
    if PAPER_TRADING:
        log("PAPER TRADING MODE ACTIVE — no real money at risk")
    log(f"Unified Kalshi Weather Daemon — {len(SERIES)} cities")
    log(f"Providers: {len(weather_ensemble.providers)} | "
        f"Min edge: {MIN_EDGE_CENTS}c | Min confidence: {MIN_CONFIDENCE_SCORE}")
    log(f"Cities: {', '.join(sorted(SERIES.keys()))}")

    state = load_state()

    # Always fetch real balance (even in paper mode) for reference
    real_balance = get_real_balance()
    state['balance'] = real_balance

    if PAPER_TRADING:
        if 'paper_balance' not in state:
            state['paper_balance'] = 10000  # $100 simulated
        log(f"Paper balance: ${state['paper_balance']/100:.2f} | "
            f"Real account: ${real_balance/100:.2f}")
    else:
        log(f"Account balance: ${real_balance/100:.2f}")

    while True:
        try:
            check_settled(state)
            save_state(state)

            opps = find_opportunities()
            if opps:
                log(f"Found {len(opps)} opportunities "
                    f"(best: {opps[0]['city']} edge={opps[0]['adjusted_edge']:.1f}c)")
                for i, o in enumerate(opps[:5]):
                    log(f"  #{i+1}: {o['side']} {o['ticker']} "
                        f"edge={o['adjusted_edge']:.1f}c conf={o['confidence']:.2f}")
                execute_trades(opps, state)
            else:
                log("No opportunities above threshold")

            save_state(state)
        except Exception as e:
            log(f"ERROR in main loop: {e}")
            traceback.print_exc()

        if PAPER_TRADING:
            save_state(state)

        interval = get_poll_interval()
        log(f"Sleeping {interval // 60} min (smart poll)...")
        time.sleep(interval)


if __name__ == '__main__':
    main()
