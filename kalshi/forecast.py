"""
Ensemble forecast interface.

Wraps weather_providers.build_ensemble() and adds NOAA staleness detection
and model-bias correction.
"""
import requests
from datetime import datetime, timezone

from weather_providers import build_ensemble, CITY_CONFIGS
from kalshi.config import NOAA_STALE_HOURS, NOAA_STALE_PENALTY, MODEL_BIAS
from kalshi.logger import log

# Singleton ensemble instance (providers are stateless HTTP callers)
weather_ensemble = build_ensemble()


def get_noaa_update_age_hours(city_cfg):
    """Return hours since NOAA last updated this grid's forecast, or None."""
    try:
        office = city_cfg['noaa_office']
        gx, gy = city_cfg['noaa_grid_x'], city_cfg['noaa_grid_y']
        url = f"https://api.weather.gov/gridpoints/{office}/{gx},{gy}/forecast"
        r = requests.get(url, headers={'User-Agent': 'KaelWeatherBot/2.0'}, timeout=10)
        r.raise_for_status()
        update_str = r.json().get('properties', {}).get('updateTime')
        if update_str:
            update_dt = datetime.fromisoformat(update_str.replace('Z', '+00:00'))
            age = datetime.now(timezone.utc) - update_dt
            return age.total_seconds() / 3600.0
    except Exception as e:
        print(f"[WARN] NOAA update age check failed for {city_cfg.get('name', '?')}: {e}")
    return None


def get_staleness_adjusted_forecast(city_cfg, target_date, city_code=None):
    """Get an ensemble forecast, penalising NOAA weight when stale.

    Returns (forecast_temp, ensemble_details) — same shape as
    WeatherEnsemble.get_ensemble_forecast().
    """
    noaa_age = get_noaa_update_age_hours(city_cfg)
    noaa_stale = noaa_age is not None and noaa_age > NOAA_STALE_HOURS

    weight_overrides = None
    if noaa_stale:
        weight_overrides = {'NOAA': NOAA_STALE_PENALTY}
        log(f"  NOAA stale ({noaa_age:.1f}h) — applying {NOAA_STALE_PENALTY}x weight penalty")

    ensemble_temp, details = weather_ensemble.get_ensemble_forecast(
        city_cfg, target_date,
        city_code=city_code,
        model_bias=MODEL_BIAS,
        weight_overrides=weight_overrides,
    )

    if details:
        details['noaa_age_hours'] = round(noaa_age, 1) if noaa_age is not None else None
        details['noaa_stale'] = noaa_stale

    return ensemble_temp, details


def get_poll_interval():
    """Return a smart polling interval (seconds) based on model-update times."""
    hour = datetime.now().hour
    if hour in (4, 5, 10, 11, 16, 17, 22, 23):
        return 300   # 5 min during model updates
    elif hour in (6, 7, 12, 13, 18, 19):
        return 600   # 10 min shortly after
    else:
        return 1800  # 30 min during quiet periods
