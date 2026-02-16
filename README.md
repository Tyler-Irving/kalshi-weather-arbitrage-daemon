# Kalshi Weather Arbitrage Bot 🌤️⚡

An automated trading bot that finds arbitrage opportunities on Kalshi prediction markets by comparing market prices against ensemble weather forecasts from multiple providers (NOAA, GFS, ICON, ECMWF, GEM).

## ⚠️ Financial Risk Warning

**This bot trades real money.** You can lose your entire account balance.

- ✅ **Paper trading is STRONGLY recommended** for testing and learning
- 🧪 Set `PAPER_TRADING=true` in your `.env` file (default)
- 💰 Start with small amounts if going live ($10-50 max)
- 📚 This is educational software, not financial advice
- ⚖️ No warranty or liability for losses

**By using this software, you accept full responsibility for any financial outcomes.**

---

## Features

### Core Trading
- **11-city coverage:** PHX, SFO, SEA, DC, HOU, NOLA, DAL, BOS, OKC, ATL, MIN
- **5-provider ensemble forecasting:** NOAA, Open-Meteo GFS, ICON, ECMWF, GEM
- **Smart edge detection:** Bayesian market blending (30% model, 70% market)
- **Risk management:**
  - Quarter-Kelly position sizing
  - Daily/weekly loss limits
  - Correlation group limits
  - Circuit breaker alerts
  - Position deduplication

### Analytics
- Season×city-specific standard deviations
- Provider bias correction (learns from settlements)
- Lead-time accuracy scaling (same-day forecasts 2x more accurate)
- Staleness detection for NOAA forecasts
- Backtest logging (JSONL format)

### Safety
- **Paper trading mode** (simulated trades, no API calls)
- Preflight checklist validation
- Edge sanity caps (prevents stale market trades)
- Spread-adjusted edge calculation
- Strike proximity filters

---

## Quick Start

### 1. Install Dependencies

```bash
# Create virtual environment
python3 -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install requirements
pip install -r requirements.txt
```

### 2. Get Kalshi API Credentials

1. Sign up at [kalshi.com](https://kalshi.com)
2. Go to **Account → API Keys**
3. Generate a new API key
4. Download the private key file (`kalshi_private.pem`)
5. Copy your API Key ID

### 3. Configure Environment

```bash
# Copy example config
cp .env.example .env

# Edit .env with your credentials
nano .env
```

**Minimal .env:**
```bash
KALSHI_API_KEY_ID=your_api_key_id_here
KALSHI_PRIVATE_KEY_PATH=./kalshi_private.pem
PAPER_TRADING=true
```

### 4. Run Paper Trading (Safe Mode)

```bash
# Make sure PAPER_TRADING=true in .env
python kalshi_unified.py
```

You should see:
```
🧪 PAPER TRADING MODE ACTIVE — No real money at risk
Unified Kalshi Weather Daemon — 11 cities
```

---

## Configuration

### Trading Parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| `PAPER_TRADING` | `true` | Safe mode (no real trades) |
| `MAX_CONTRACTS` | 8 | Max contracts per trade |
| `MAX_COST_PER_TRADE` | 500¢ ($5) | Max cost per trade |
| `MAX_OPEN_POSITIONS` | 20 | Max simultaneous positions |
| `MAX_DAILY_TRADES` | 40 | Daily trade limit |
| `MIN_EDGE_CENTS` | 10¢ | Minimum edge to trade (paper) |
| `MIN_CONFIDENCE_SCORE` | 0.5 | Minimum forecast confidence (paper) |
| `POLL_INTERVAL` | 900s (15min) | Scan interval |

### Risk Limits

| Parameter | Default | Description |
|-----------|---------|-------------|
| `MAX_DAILY_LOSS_CENTS` | 500¢ ($5) | Daily stop-loss |
| `MAX_WEEKLY_LOSS_CENTS` | 1000¢ ($10) | Weekly stop-loss |
| `MAX_PER_GROUP` | 2 | Max per correlation group |
| `MAX_PER_CITY_DATE` | 1 | Max per city per settlement date |

---

## Going Live (Advanced)

### ⚠️ Read This First

Live trading is **high risk**. Only proceed if you:
- ✅ Understand prediction markets and weather forecasting
- ✅ Have tested paper trading for at least 1-2 weeks
- ✅ Are comfortable losing your entire account balance
- ✅ Have read the entire codebase

### Pre-Flight Checklist

```bash
python preflight_checklist.py
```

This validates:
- API credentials are valid
- Account balance sufficient
- Network connectivity
- Configuration safety checks

### Enable Live Trading

1. **Set environment variable:**
```bash
# In .env
PAPER_TRADING=false
```

2. **Start with minimal capital ($10-20)**
3. **Monitor closely for first 24 hours**
4. **Check logs regularly:** `tail -f kalshi_unified_log.txt`

---

## File Structure

```
kalshi-weather-bot/
├── kalshi_unified.py          # Main trading daemon
├── weather_providers.py       # Ensemble forecast providers
├── paper_trading_safety.py    # Paper trading mock implementation
├── requirements.txt           # Python dependencies
├── .env.example              # Config template
├── .gitignore                # Git exclusions
├── README.md                 # This file
├── kalshi_private.pem        # YOUR private key (gitignored)
└── .env                      # YOUR config (gitignored)
```

### Generated Files (Gitignored)

```
kalshi_unified_log.txt        # Trading log
kalshi_unified_state.json     # Position state
kalshi_pnl.json              # P&L tracking
kalshi_backtest_log.jsonl    # Backtest data
kalshi_settlement_log.jsonl  # Settlement history
paper_trades.jsonl           # Paper trading log
```

---

## Optional: Telegram Notifications

Get real-time trade alerts via Telegram.

### Setup

1. **Create a bot:**
   - Message [@BotFather](https://t.me/BotFather) on Telegram
   - Send `/newbot` and follow prompts
   - Copy the bot token

2. **Get your chat ID:**
   - Message your bot
   - Visit: `https://api.telegram.org/bot<YOUR_BOT_TOKEN>/getUpdates`
   - Find your `chat.id` in the JSON

3. **Add to .env:**
```bash
TELEGRAM_BOT_TOKEN=123456789:ABCdefGHIjklMNOpqrsTUVwxyz
TELEGRAM_CHAT_ID=123456789
```

### Notification Types

- 📝 **Trade Opened** (paper/live)
- 🎯 **Position Settled** (win/loss)
- 📅 **Daily Summary** (P&L, win rate)
- 🚨 **System Alerts** (circuit breaker, errors)

---

## Strategy Overview

### Ensemble Forecasting

The bot aggregates forecasts from 5 providers:
1. **NOAA** (National Weather Service) — weight: 1.5
2. **GFS** (Global Forecast System) — weight: 1.0
3. **ICON** (German Weather Service) — weight: 1.2
4. **ECMWF** (European model) — weight: 1.3
5. **GEM** (Canadian model) — weight: 1.0

Weighted average with optional bias correction.

### Edge Calculation

```
Model Probability → Bayesian Blend with Market → Edge = (Blended Fair - Market Price)
```

- **Model Weight:** 30% (configurable)
- **Market Weight:** 70% (assumes efficient pricing)
- **Confidence Adjustment:** Edge scaled by forecast confidence

### Filters

1. **Price floors:** Reject cheap tail bets (<15¢)
2. **Strike proximity:** Skip when forecast near strike (<1.5°F)
3. **Disagreement:** Skip if model vs market delta >25¢
4. **Ratio filter:** Skip if fair/market ratio >3x
5. **Spread filter:** Skip illiquid markets (spread >30¢)
6. **Provider spread:** Skip if forecasts disagree >6°F

### Position Sizing

**Quarter-Kelly criterion:**
```
f* = (p × b - q) / b × 0.25
```
Where:
- `p` = fair probability
- `q` = 1 - p
- `b` = odds ratio (payout/cost)
- `0.25` = Kelly fraction (conservative)

Capped by `MAX_COST_PER_TRADE` and `MAX_CONTRACTS`.

---

## Monitoring

### Logs

```bash
# Live tail
tail -f kalshi_unified_log.txt

# Search for trades
grep "TRADE:" kalshi_unified_log.txt

# Check settlements
grep "SETTLED:" kalshi_unified_log.txt
```

### State Inspection

```bash
# Check current positions
cat kalshi_unified_state.json | jq '.positions'

# Check P&L
cat kalshi_pnl.json | jq
```

### Paper Trading Summary

```bash
python paper_summary.py
```

Shows simulated results, win rate, and ROI.

---

## Troubleshooting

### "Invalid API credentials"
- Check `KALSHI_API_KEY_ID` is correct
- Ensure `kalshi_private.pem` exists and matches your account
- Try regenerating API key from Kalshi dashboard

### "NOAA stale (8.5h) — applying penalty"
- NOAA updates every ~6 hours
- Weight automatically reduced during stale periods
- GFS/ICON/ECMWF continue to provide fresh forecasts

### "Circuit breaker activated"
- Daily or weekly loss limit reached
- Trading paused until next period
- Check `MAX_DAILY_LOSS_CENTS` in `.env`

### No opportunities found
- Markets may be efficiently priced
- Try lowering `MIN_EDGE_CENTS` (paper trading only)
- Check if settlement dates are too far out (>5 days)

---

## Development

### Adding a New City

1. **Find NOAA grid coordinates:**
   - Visit [weather.gov](https://weather.gov)
   - Search for city
   - Note the office code + grid X/Y from forecast URL

2. **Find NOAA station ID:**
   - Visit [NOAA stations list](https://www.weather.gov/documentation/services-web-api)
   - Find nearest observation station

3. **Add to `CITY_CONFIGS` in `weather_providers.py`:**
```python
'NYC': {
    'name': 'New York City',
    'lat': 40.7128,
    'lon': -74.0060,
    'noaa_office': 'OKX',
    'noaa_grid_x': 33,
    'noaa_grid_y': 37,
    'station': 'KNYC'
}
```

4. **Add to `SERIES` in `kalshi_unified.py`:**
```python
'NYC': 'KXHIGHTNYC'
```

### Running Tests

```bash
# Validate paper trading setup
python validate_paper_setup.py

# Test single trade cycle
python test_paper_mode.py

# Backtest validation
python backtest_validation.py
```

---

## Dashboard (Optional)

Real-time web dashboard for monitoring positions, P&L, and reliability metrics.

See `dashboard/README.md` for setup instructions.

**Tech stack:** React + Django + WebSockets

---

## Contributing

Contributions welcome! Areas for improvement:
- Additional weather providers (NWS, Weather Underground)
- Machine learning confidence models
- Portfolio optimization
- Backtesting framework enhancements
- Dashboard improvements

Please open an issue before major changes.

---

## License

MIT License - See LICENSE file for details.

---

## Acknowledgments

- Weather data: NOAA, Open-Meteo, DWD (ICON), ECMWF, CMC (GEM)
- Market data: Kalshi API
- Inspired by statistical arbitrage and ensemble forecasting research

---

## Disclaimer

This software is provided "as-is" for educational purposes. No warranty, express or implied. Not financial advice. Use at your own risk. The author is not responsible for any financial losses incurred through use of this software.

Prediction markets involve substantial risk. Past performance does not guarantee future results.

---

## Contact

- **Issues:** [GitHub Issues](https://github.com/Tyler-Irving/kalshi-weather-bot/issues)
- **Discussions:** [GitHub Discussions](https://github.com/Tyler-Irving/kalshi-weather-bot/discussions)

---

**Happy trading! 🌦️📈**
