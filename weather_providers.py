#!/usr/bin/env python3
"""
Modular weather data providers for ensemble forecasting.
Each provider implements the same interface for easy swapping/comparison.
"""
import os
import requests
import json
import time
from abc import ABC, abstractmethod
from datetime import datetime, timedelta
from typing import Dict, Optional, List, Tuple
import logging

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class WeatherProvider(ABC):
    """Abstract base class for weather data providers."""
    
    def __init__(self, name: str):
        self.name = name
        self.last_request_time = 0
        self.rate_limit_delay = 0.3  # seconds between requests
        
    @abstractmethod
    def get_forecast_high(self, location: Dict, target_date: datetime) -> Optional[float]:
        """Get the forecasted high temperature for a specific date."""
        pass
        
    def _rate_limit(self):
        """Basic rate limiting."""
        elapsed = time.time() - self.last_request_time
        if elapsed < self.rate_limit_delay:
            time.sleep(self.rate_limit_delay - elapsed)
        self.last_request_time = time.time()


class NOAAProvider(WeatherProvider):
    """NOAA National Weather Service provider."""

    def __init__(self):
        super().__init__("NOAA")
        self.base_url = "https://api.weather.gov"
        self.last_update_time = None  # ISO string from most recent NOAA response

    def get_forecast_high(self, location: Dict, target_date: datetime) -> Optional[float]:
        """Get NOAA forecast for target date."""
        try:
            self._rate_limit()
            office = location['noaa_office']
            grid_x = location['noaa_grid_x']
            grid_y = location['noaa_grid_y']

            url = f"{self.base_url}/gridpoints/{office}/{grid_x},{grid_y}/forecast"
            headers = {'User-Agent': 'KaelWeatherBot/2.0'}

            response = requests.get(url, headers=headers, timeout=15)
            response.raise_for_status()

            data = response.json()
            # Cache the NOAA updateTime so staleness can be checked without
            # a separate HTTP request (see forecast.py).
            self.last_update_time = data.get('properties', {}).get('updateTime')
            periods = data['properties']['periods']
            
            target_date_str = target_date.strftime("%Y-%m-%d")
            
            for period in periods:
                if period.get('isDaytime', True):  # Only daytime periods have high temps
                    period_time = datetime.fromisoformat(period['startTime'].replace('Z', '+00:00'))
                    period_date_str = period_time.strftime("%Y-%m-%d")
                    
                    if period_date_str == target_date_str:
                        temp_f = period['temperature']
                        if period['temperatureUnit'] == 'C':
                            temp_f = (temp_f * 9/5) + 32
                        logger.info(f"NOAA forecast for {target_date_str}: {temp_f}°F")
                        return temp_f
            
            logger.warning(f"NOAA: No forecast found for {target_date_str}")
            return None
            
        except Exception as e:
            logger.error(f"NOAA provider error: {e}")
            return None


class OpenMeteoGFSProvider(WeatherProvider):
    """Open-Meteo GFS model — free, no API key."""

    def __init__(self):
        super().__init__("OpenMeteo_GFS")
        self.base_url = "https://api.open-meteo.com/v1/gfs"

    def get_forecast_high(self, location: Dict, target_date: datetime) -> Optional[float]:
        try:
            self._rate_limit()
            date_str = target_date.strftime("%Y-%m-%d")
            params = {
                'latitude': location['lat'],
                'longitude': location['lon'],
                'daily': 'temperature_2m_max',
                'temperature_unit': 'fahrenheit',
                'start_date': date_str,
                'end_date': date_str,
                'timezone': location.get('timezone', 'auto'),
            }
            r = requests.get(self.base_url, params=params, timeout=15)
            r.raise_for_status()
            temps = r.json().get('daily', {}).get('temperature_2m_max', [])
            if temps and temps[0] is not None:
                logger.info(f"OpenMeteo GFS forecast for {date_str}: {temps[0]}°F")
                return float(temps[0])
            return None
        except Exception as e:
            logger.error(f"OpenMeteo GFS error: {e}")
            return None


class OpenMeteoICONProvider(WeatherProvider):
    """Open-Meteo DWD ICON model — free, no API key."""

    def __init__(self):
        super().__init__("OpenMeteo_ICON")
        self.base_url = "https://api.open-meteo.com/v1/dwd-icon"

    def get_forecast_high(self, location: Dict, target_date: datetime) -> Optional[float]:
        try:
            self._rate_limit()
            date_str = target_date.strftime("%Y-%m-%d")
            params = {
                'latitude': location['lat'],
                'longitude': location['lon'],
                'daily': 'temperature_2m_max',
                'temperature_unit': 'fahrenheit',
                'start_date': date_str,
                'end_date': date_str,
                'timezone': location.get('timezone', 'auto'),
            }
            r = requests.get(self.base_url, params=params, timeout=15)
            r.raise_for_status()
            temps = r.json().get('daily', {}).get('temperature_2m_max', [])
            if temps and temps[0] is not None:
                logger.info(f"OpenMeteo ICON forecast for {date_str}: {temps[0]}°F")
                return float(temps[0])
            return None
        except Exception as e:
            logger.error(f"OpenMeteo ICON error: {e}")
            return None


class OpenMeteoECMWFProvider(WeatherProvider):
    """Open-Meteo ECMWF IFS model — free, no API key."""

    def __init__(self):
        super().__init__("OpenMeteo_ECMWF")
        self.base_url = "https://api.open-meteo.com/v1/ecmwf"

    def get_forecast_high(self, location: Dict, target_date: datetime) -> Optional[float]:
        try:
            self._rate_limit()
            date_str = target_date.strftime("%Y-%m-%d")
            params = {
                'latitude': location['lat'],
                'longitude': location['lon'],
                'daily': 'temperature_2m_max',
                'temperature_unit': 'fahrenheit',
                'start_date': date_str,
                'end_date': date_str,
                'timezone': location.get('timezone', 'auto'),
            }
            r = requests.get(self.base_url, params=params, timeout=15)
            r.raise_for_status()
            temps = r.json().get('daily', {}).get('temperature_2m_max', [])
            if temps and temps[0] is not None:
                logger.info(f"OpenMeteo ECMWF forecast for {date_str}: {temps[0]}°F")
                return float(temps[0])
            return None
        except Exception as e:
            logger.error(f"OpenMeteo ECMWF error: {e}")
            return None


class OpenMeteoGEMProvider(WeatherProvider):
    """Open-Meteo GEM (Canadian) model — free, no API key."""

    def __init__(self):
        super().__init__("OpenMeteo_GEM")
        self.base_url = "https://api.open-meteo.com/v1/gem"

    def get_forecast_high(self, location: Dict, target_date: datetime) -> Optional[float]:
        try:
            self._rate_limit()
            date_str = target_date.strftime("%Y-%m-%d")
            params = {
                'latitude': location['lat'],
                'longitude': location['lon'],
                'daily': 'temperature_2m_max',
                'temperature_unit': 'fahrenheit',
                'start_date': date_str,
                'end_date': date_str,
                'timezone': location.get('timezone', 'auto'),
            }
            r = requests.get(self.base_url, params=params, timeout=15)
            r.raise_for_status()
            temps = r.json().get('daily', {}).get('temperature_2m_max', [])
            if temps and temps[0] is not None:
                logger.info(f"OpenMeteo GEM forecast for {date_str}: {temps[0]}°F")
                return float(temps[0])
            return None
        except Exception as e:
            logger.error(f"OpenMeteo GEM error: {e}")
            return None


class OpenWeatherMapProvider(WeatherProvider):
    """OpenWeatherMap provider (requires API key)."""
    
    def __init__(self, api_key: Optional[str] = None):
        super().__init__("OpenWeatherMap")
        self.api_key = api_key
        self.base_url = "https://api.openweathermap.org/data/3.0"
        
    def get_forecast_high(self, location: Dict, target_date: datetime) -> Optional[float]:
        """Get OpenWeatherMap forecast for target date."""
        if not self.api_key:
            logger.warning("OpenWeatherMap: No API key provided")
            return None
            
        try:
            self._rate_limit()
            lat = location['lat']
            lon = location['lon']
            
            url = f"{self.base_url}/onecall"
            params = {
                'lat': lat,
                'lon': lon,
                'appid': self.api_key,
                'units': 'imperial',
                'exclude': 'current,minutely,hourly,alerts'
            }
            
            response = requests.get(url, params=params, timeout=15)
            response.raise_for_status()
            
            data = response.json()
            daily_forecasts = data['daily']
            
            target_timestamp = int(target_date.timestamp())
            
            for day in daily_forecasts:
                day_timestamp = day['dt']
                forecast_date = datetime.fromtimestamp(day_timestamp).date()
                
                if forecast_date == target_date.date():
                    temp_high = day['temp']['max']
                    logger.info(f"OpenWeatherMap forecast for {target_date.date()}: {temp_high}°F")
                    return temp_high
            
            logger.warning(f"OpenWeatherMap: No forecast found for {target_date.date()}")
            return None
            
        except Exception as e:
            logger.error(f"OpenWeatherMap provider error: {e}")
            return None


class WeatherAPIProvider(WeatherProvider):
    """WeatherAPI.com provider (requires API key)."""
    
    def __init__(self, api_key: Optional[str] = None):
        super().__init__("WeatherAPI")
        self.api_key = api_key
        self.base_url = "http://api.weatherapi.com/v1"
        
    def get_forecast_high(self, location: Dict, target_date: datetime) -> Optional[float]:
        """Get WeatherAPI forecast for target date."""
        if not self.api_key:
            logger.warning("WeatherAPI: No API key provided")
            return None
            
        try:
            self._rate_limit()
            lat = location['lat']
            lon = location['lon']
            
            # WeatherAPI uses days parameter for forecast range
            days_ahead = (target_date.date() - datetime.now().date()).days
            if days_ahead < 0 or days_ahead > 10:
                logger.warning(f"WeatherAPI: Target date {target_date.date()} out of range")
                return None
                
            url = f"{self.base_url}/forecast.json"
            params = {
                'key': self.api_key,
                'q': f"{lat},{lon}",
                'days': max(days_ahead + 1, 3),
                'aqi': 'no',
                'alerts': 'no'
            }
            
            response = requests.get(url, params=params, timeout=15)
            response.raise_for_status()
            
            data = response.json()
            forecast_days = data['forecast']['forecastday']
            
            target_date_str = target_date.strftime("%Y-%m-%d")
            
            for day in forecast_days:
                if day['date'] == target_date_str:
                    temp_high = day['day']['maxtemp_f']
                    logger.info(f"WeatherAPI forecast for {target_date_str}: {temp_high}°F")
                    return temp_high
            
            logger.warning(f"WeatherAPI: No forecast found for {target_date_str}")
            return None
            
        except Exception as e:
            logger.error(f"WeatherAPI provider error: {e}")
            return None


class WeatherEnsemble:
    """Ensemble forecasting system combining multiple weather providers."""
    
    def __init__(self):
        self.providers = []
        self.accuracy_history = {}  # provider_name -> list of (error, timestamp) tuples
        self.accuracy_file = os.getenv("WEATHER_ACCURACY_PATH", "./weather_accuracy.json")
        self.load_accuracy_history()
        
    def add_provider(self, provider: WeatherProvider, weight: float = 1.0):
        """Add a weather provider to the ensemble."""
        self.providers.append((provider, weight))
        if provider.name not in self.accuracy_history:
            self.accuracy_history[provider.name] = []
    
    def get_ensemble_forecast(self, location: Dict, target_date: datetime, city_code: str = None, model_bias: Dict = None, weight_overrides: Dict = None) -> Tuple[Optional[float], Dict]:
        """Get ensemble forecast combining all providers.
        
        Args:
            city_code: Optional city code for model bias correction.
            model_bias: Optional dict of (provider_name, city_code) -> bias_degrees.
            weight_overrides: Optional dict of provider_name -> weight_multiplier (e.g., {'NOAA': 0.5} to halve NOAA weight).
                             FIX-018: Avoids mutable global state by passing overrides.
        """
        forecasts = {}
        weights = {}
        
        for provider, base_weight in self.providers:
            forecast = provider.get_forecast_high(location, target_date)
            if forecast is not None:
                # Apply model bias correction if available
                if model_bias and city_code:
                    bias = model_bias.get((provider.name, city_code), 0.0)
                    forecast = forecast - bias
                # Adjust weight based on historical accuracy
                adjusted_weight = self._get_adjusted_weight(provider.name, base_weight)
                
                # FIX-018: Apply weight override if specified (e.g., for NOAA staleness penalty)
                if weight_overrides and provider.name in weight_overrides:
                    adjusted_weight *= weight_overrides[provider.name]
                
                forecasts[provider.name] = forecast
                weights[provider.name] = adjusted_weight
        
        if not forecasts:
            return None, {}
        
        # Weighted ensemble average
        total_weight = sum(weights.values())
        ensemble_forecast = sum(
            forecasts[name] * weights[name] for name in forecasts
        ) / total_weight
        
        result_details = {
            'ensemble_forecast': ensemble_forecast,
            'individual_forecasts': forecasts,
            'weights': weights,
            'provider_count': len(forecasts)
        }
        
        logger.info(f"Ensemble forecast: {ensemble_forecast:.1f}°F from {len(forecasts)} providers")
        return ensemble_forecast, result_details
    
    def get_noaa_update_age_hours(self) -> Optional[float]:
        """Return hours since the NOAA provider's last forecast updateTime.

        Uses the cached updateTime from the most recent get_ensemble_forecast()
        call, avoiding an extra HTTP request.
        """
        for provider, _ in self.providers:
            if isinstance(provider, NOAAProvider) and provider.last_update_time:
                try:
                    update_dt = datetime.fromisoformat(
                        provider.last_update_time.replace('Z', '+00:00')
                    )
                    from datetime import timezone
                    age = datetime.now(timezone.utc) - update_dt
                    return age.total_seconds() / 3600.0
                except Exception:
                    return None
        return None

    def record_accuracy(self, provider_name: str, predicted: float, actual: float):
        """Record actual vs predicted for a provider."""
        error = abs(predicted - actual)
        timestamp = time.time()
        
        if provider_name not in self.accuracy_history:
            self.accuracy_history[provider_name] = []
        
        self.accuracy_history[provider_name].append((error, timestamp))
        
        # Keep only last 100 records per provider
        self.accuracy_history[provider_name] = self.accuracy_history[provider_name][-100:]
        
        self.save_accuracy_history()
        logger.info(f"Recorded {provider_name} accuracy: {error:.1f}°F error")
    
    def _get_adjusted_weight(self, provider_name: str, base_weight: float) -> float:
        """Adjust provider weight based on historical accuracy."""
        if provider_name not in self.accuracy_history:
            return base_weight
        
        history = self.accuracy_history[provider_name]
        if len(history) < 5:  # Need minimum history
            return base_weight
        
        # Recent accuracy (last 30 days)
        recent_cutoff = time.time() - (30 * 24 * 3600)
        recent_errors = [error for error, ts in history if ts > recent_cutoff]
        
        if not recent_errors:
            return base_weight
        
        # Lower error = higher weight
        avg_error = sum(recent_errors) / len(recent_errors)
        # Inverse relationship: error of 1°F gets weight 1.0, error of 3°F gets weight 0.33
        # Capped at 2.0x so one hot-streak provider can't dominate the ensemble,
        # and floored at 0.25x so a poor streak doesn't completely silence a provider.
        accuracy_multiplier = min(2.0, max(0.25, 1.0 / max(avg_error, 0.5)))

        return base_weight * accuracy_multiplier
    
    def load_accuracy_history(self):
        """Load accuracy history from file."""
        try:
            with open(self.accuracy_file, 'r') as f:
                self.accuracy_history = json.load(f)
        except FileNotFoundError:
            self.accuracy_history = {}
        except Exception as e:
            logger.error(f"Error loading accuracy history: {e}")
            self.accuracy_history = {}
    
    def save_accuracy_history(self):
        """Save accuracy history to file."""
        try:
            with open(self.accuracy_file, 'w') as f:
                json.dump(self.accuracy_history, f, indent=2)
        except Exception as e:
            logger.error(f"Error saving accuracy history: {e}")


# ── City configs (lat/lon + NOAA grid for all Kalshi cities) ────────────
CITY_CONFIGS = {
    'PHX': {'name': 'Phoenix',       'lat': 33.4484, 'lon': -112.0740, 'noaa_office': 'PSR', 'noaa_grid_x': 162, 'noaa_grid_y': 57,  'timezone': 'America/Phoenix',      'station': 'KPHX'},
    'SFO': {'name': 'San Francisco', 'lat': 37.7749, 'lon': -122.4194, 'noaa_office': 'MTR', 'noaa_grid_x': 85,  'noaa_grid_y': 105, 'timezone': 'America/Los_Angeles',  'station': 'KSFO'},
    'SEA': {'name': 'Seattle',       'lat': 47.6062, 'lon': -122.3321, 'noaa_office': 'SEW', 'noaa_grid_x': 124, 'noaa_grid_y': 67,  'timezone': 'America/Los_Angeles',  'station': 'KSEA'},
    'DC':  {'name': 'Washington DC', 'lat': 38.9072, 'lon': -77.0369,  'noaa_office': 'LWX', 'noaa_grid_x': 96,  'noaa_grid_y': 70,  'timezone': 'America/New_York',     'station': 'KDCA'},
    'HOU': {'name': 'Houston',       'lat': 29.7604, 'lon': -95.3698,  'noaa_office': 'HGX', 'noaa_grid_x': 65,  'noaa_grid_y': 97,  'timezone': 'America/Chicago',      'station': 'KIAH'},
    'NOLA':{'name': 'New Orleans',   'lat': 29.9511, 'lon': -90.0715,  'noaa_office': 'LIX', 'noaa_grid_x': 76,  'noaa_grid_y': 72,  'timezone': 'America/Chicago',      'station': 'KMSY'},
    'DAL': {'name': 'Dallas',        'lat': 32.7767, 'lon': -96.7970,  'noaa_office': 'FWD', 'noaa_grid_x': 80,  'noaa_grid_y': 108, 'timezone': 'America/Chicago',      'station': 'KDFW'},
    'BOS': {'name': 'Boston',        'lat': 42.3601, 'lon': -71.0589,  'noaa_office': 'BOX', 'noaa_grid_x': 70,  'noaa_grid_y': 76,  'timezone': 'America/New_York',     'station': 'KBOS'},
    'OKC': {'name': 'Oklahoma City', 'lat': 35.4676, 'lon': -97.5164,  'noaa_office': 'OUN', 'noaa_grid_x': 41,  'noaa_grid_y': 48,  'timezone': 'America/Chicago',      'station': 'KOKC'},
    'ATL': {'name': 'Atlanta',        'lat': 33.7490, 'lon': -84.3880,  'noaa_office': 'FFC', 'noaa_grid_x': 52,  'noaa_grid_y': 88,  'timezone': 'America/New_York',     'station': 'KATL'},
    'MIN': {'name': 'Minneapolis',    'lat': 44.9778, 'lon': -93.2650,  'noaa_office': 'MPX', 'noaa_grid_x': 107, 'noaa_grid_y': 71,  'timezone': 'America/Chicago',      'station': 'KMSP'},
}

# Legacy alias
PHOENIX_CONFIG = CITY_CONFIGS['PHX']


def build_ensemble() -> WeatherEnsemble:
    """Build the standard 5-provider ensemble (all free)."""
    ensemble = WeatherEnsemble()
    ensemble.add_provider(NOAAProvider(), weight=1.2)           # Slight NOAA premium — gold standard for US
    ensemble.add_provider(OpenMeteoGFSProvider(), weight=1.0)
    ensemble.add_provider(OpenMeteoICONProvider(), weight=0.9)  # EU model, slightly less US-tuned
    ensemble.add_provider(OpenMeteoECMWFProvider(), weight=1.0)
    ensemble.add_provider(OpenMeteoGEMProvider(), weight=0.8)   # Canadian model, less US-optimized
    return ensemble


def test_ensemble():
    """Test the ensemble system across all cities."""
    from datetime import datetime, timedelta

    ensemble = build_ensemble()
    tomorrow = datetime.now() + timedelta(days=1)

    print(f"\n{'='*60}")
    print(f"  Ensemble Forecast Test — {tomorrow.strftime('%Y-%m-%d')}")
    print(f"{'='*60}")

    for code, cfg in CITY_CONFIGS.items():
        forecast, details = ensemble.get_ensemble_forecast(cfg, tomorrow)
        if forecast:
            individuals = ", ".join(f"{k}: {v:.0f}°F" for k, v in details['individual_forecasts'].items())
            print(f"  {code:5s} | Ensemble: {forecast:.1f}°F | {individuals}")
        else:
            print(f"  {code:5s} | NO DATA")
        time.sleep(0.3)  # be kind to APIs

    print(f"{'='*60}\n")


if __name__ == "__main__":
    test_ensemble()