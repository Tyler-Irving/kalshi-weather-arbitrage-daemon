"""
Telegram notification helpers.

Each public function sends a single rich-HTML message for a specific event
type (trade opened, settlement, daily summary, system alert).
"""
import requests

from kalshi.config import TG_BOT_TOKEN, TG_CHAT_ID, PAPER_TRADING_NOTIFICATIONS


def _send(msg):
    """Low-level Telegram sendMessage wrapper."""
    if not TG_BOT_TOKEN:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage",
            json={"chat_id": TG_CHAT_ID, "text": msg, "parse_mode": "HTML"},
            timeout=10,
        )
    except Exception as e:
        print(f"[WARN] Telegram notification failed: {e}")


# ── Public notification functions ────────────────────────────────────────

def notify_trade_opened(data):
    """Send a trade-opened notification.

    Expected keys: ticker, side, count, price, description, forecast,
    provider_count, confidence, edge, cost, is_paper.
    """
    if data.get('is_paper') and not PAPER_TRADING_NOTIFICATIONS:
        return

    emoji = "\U0001f4dd" if data.get('is_paper') else "\u2705"
    label = "<b>Paper Trade</b>" if data.get('is_paper') else "<b>Trade Executed</b>"

    msg = (
        f"{emoji} {label}\n\n"
        f"<b>Contract:</b> {data['ticker']}\n"
        f"<b>Side:</b> {data['side'].upper()}\n"
        f"<b>Quantity:</b> {data['count']}x @ {data['price']}\u00a2\n\n"
        f"<b>Details:</b>\n"
        f"\u2022 {data['description']}\n"
        f"\u2022 Forecast: {data['forecast']:.1f}\u00b0F ({data['provider_count']} providers)\n\n"
        f"<b>Analysis:</b>\n"
        f"\u2022 Confidence: {data['confidence']:.0%}\n"
        f"\u2022 Edge: {data['edge']:.1f}\u00a2\n"
        f"\u2022 Cost: ${data['cost']/100:.2f}"
    )
    _send(msg)


def notify_settlement(data):
    """Send a settlement notification.

    Expected keys: ticker, won, pnl_cents, total_pnl_cents, is_paper.
    Optional: actual_temp, forecast.
    """
    if data.get('is_paper') and not PAPER_TRADING_NOTIFICATIONS:
        return

    won = data['won']
    emoji = "\U0001f3af" if won else "\U0001f4c9"
    outcome = "WIN" if won else "LOSS"
    pnl = data['pnl_cents']
    label = "Paper Position" if data.get('is_paper') else "Position"

    msg = (
        f"{emoji} <b>{label} Settled</b>\n\n"
        f"<b>Contract:</b> {data['ticker']}\n"
        f"<b>Outcome:</b> {outcome}\n"
        f"<b>P&L:</b> ${pnl/100:+.2f}"
    )

    if data.get('actual_temp') and data.get('forecast'):
        error = abs(data['actual_temp'] - data['forecast'])
        msg += (
            f"\n\n<b>Temperatures:</b>\n"
            f"\u2022 Forecast: {data['forecast']:.1f}\u00b0F\n"
            f"\u2022 Actual: {data['actual_temp']:.1f}\u00b0F\n"
            f"\u2022 Error: {error:.1f}\u00b0F"
        )
    elif data.get('actual_temp'):
        msg += f"\n\u2022 Actual temp: {data['actual_temp']:.1f}\u00b0F"

    total = data['total_pnl_cents']
    pnl_emoji = "\U0001f4b0" if total >= 0 else "\u26a0\ufe0f"
    bucket = "Paper" if data.get('is_paper') else "Total"
    msg += f"\n\n{pnl_emoji} <b>{bucket} P&L:</b> ${total/100:+.2f}"

    _send(msg)


def notify_daily_summary(data):
    """Send an end-of-day summary.

    Expected keys: date, trades, wins, losses, pnl_cents,
    total_pnl_cents, open_positions, balance, is_paper.
    """
    trades = data['trades']
    wins = data['wins']
    losses = data['losses']
    win_rate = (wins / trades * 100) if trades > 0 else 0
    pnl = data['pnl_cents']
    pnl_emoji = "\U0001f4c8" if pnl >= 0 else "\U0001f4c9"
    label = "Paper Trading" if data.get('is_paper') else "Trading"

    msg = (
        f"\U0001f4c5 <b>Daily {label} Summary</b>\n\n"
        f"<b>Date:</b> {data['date']}\n\n"
        f"<b>Activity:</b>\n"
        f"\u2022 Trades: {trades}\n"
        f"\u2022 Record: {wins}W-{losses}L ({win_rate:.0f}%)\n"
        f"\u2022 Daily P&L: ${pnl/100:+.2f} {pnl_emoji}\n\n"
        f"<b>Portfolio:</b>\n"
        f"\u2022 Open positions: {data['open_positions']}\n"
        f"\u2022 Balance: ${data['balance']/100:.2f}\n"
        f"\u2022 Total P&L: ${data['total_pnl_cents']/100:+.2f}"
    )
    _send(msg)


def notify_system_alert(data):
    """Send a system alert / error notification.

    Expected keys: level ('info'|'warning'|'error'|'critical'),
    title, message.  Optional: details.
    """
    emoji_map = {
        'info': '\u2139\ufe0f',
        'warning': '\u26a0\ufe0f',
        'error': '\U0001f534',
        'critical': '\U0001f6a8',
    }
    emoji = emoji_map.get(data.get('level', 'info'), '\u2139\ufe0f')

    msg = f"{emoji} <b>{data['title']}</b>\n\n{data['message']}"
    if data.get('details'):
        msg += f"\n\n<b>Details:</b>\n{data['details']}"

    _send(msg)
