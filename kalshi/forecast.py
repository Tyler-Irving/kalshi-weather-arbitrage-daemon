"""
Ensemble forecast interface.

Wraps weather_providers.build_ensemble() and adds NOAA staleness detection
and model-bias correction.
"""
from datetime import datetime, timezone

from weather_providers import build_ensemble, CITY_CONFIGS
from kalshi.config import NOAA_STALE_HOURS, NOAA_STALE_PENALTY, MODEL_BIAS
from kalshi.logger import log

# Singleton ensemble instance (providers are stateless HTTP callers)
weather_ensemble = build_ensemble()


def get_staleness_adjusted_forecast(city_cfg, target_date, city_code=None):
    """Get an ensemble forecast, penalising NOAA weight when stale.

    Staleness is detected from the NOAA provider's cached updateTime
    (populated during the ensemble call itself) — no extra HTTP request.

    If stale, re-runs the ensemble with a NOAA weight penalty.

    Returns (forecast_temp, ensemble_details).
    """
    # First pass: run ensemble normally (NOAA's updateTime gets cached)
    ensemble_temp, details = weather_ensemble.get_ensemble_forecast(
        city_cfg, target_date,
        city_code=city_code,
        model_bias=MODEL_BIAS,
    )

    # Check staleness from the cached NOAA response (no extra HTTP call)
    noaa_age = weather_ensemble.get_noaa_update_age_hours()
    noaa_stale = noaa_age is not None and noaa_age > NOAA_STALE_HOURS

    if noaa_stale:
        log(f"  NOAA stale ({noaa_age:.1f}h) — re-running ensemble with {NOAA_STALE_PENALTY}x weight penalty")
        ensemble_temp, details = weather_ensemble.get_ensemble_forecast(
            city_cfg, target_date,
            city_code=city_code,
            model_bias=MODEL_BIAS,
            weight_overrides={'NOAA': NOAA_STALE_PENALTY},
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
