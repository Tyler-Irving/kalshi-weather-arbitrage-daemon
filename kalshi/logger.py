"""
Logging and JSONL file writers for trades, backtests, and settlements.
"""
import json
from datetime import datetime

from kalshi.config import LOG_PATH, MAX_LOG_LINES, BACKTEST_PATH, PAPER_TRADES_PATH


def log(msg):
    """Write a timestamped message to stdout and the rolling log file."""
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
        print(f"[ERROR] Log file write/rotation failed: {e}")


def log_backtest(entry):
    """Append one JSON line to the backtest log."""
    try:
        with open(BACKTEST_PATH, 'a') as f:
            f.write(json.dumps(entry, default=str) + "\n")
    except Exception as e:
        print(f"[WARN] Failed to write backtest entry: {e}")


def log_paper_trade(entry):
    """Append one JSON line to the paper-trades log."""
    try:
        with open(PAPER_TRADES_PATH, 'a') as f:
            f.write(json.dumps(entry, default=str) + "\n")
    except Exception as e:
        print(f"[WARN] Failed to write paper trade entry: {e}")
