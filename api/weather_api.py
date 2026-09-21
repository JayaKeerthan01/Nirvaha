"""
Weather data source.

If Config.OPENWEATHER_API_KEY is set, real current-conditions data is pulled
from OpenWeatherMap. Otherwise the module runs in SIMULATION MODE and
generates realistic, slowly-drifting synthetic weather per zone so the whole
dashboard is fully demoable with no internet connection or API key.
"""

import logging
import math
import time
import random

from config import Config

logger = logging.getLogger(__name__)

try:
    import requests  # only needed in live mode
except ImportError:
    requests = None

# Simulation state: gives each zone a persistent, slowly evolving weather
# pattern instead of pure random noise every call (feels more "live").
_sim_state = {}


def _init_zone_state(zone_name):
    rng = random.Random(zone_name)
    _sim_state[zone_name] = {
        "rainfall_mm": rng.uniform(5, 60),
        "temperature_c": rng.uniform(24, 33),
        "humidity_pct": rng.uniform(55, 85),
        "wind_speed_kmh": rng.uniform(10, 35),
        "phase": rng.uniform(0, math.pi * 2),
    }


def _drift(zone_name):
    """Random-walk the zone's weather slightly on every call, with an
    occasional storm surge so risk levels change meaningfully over time."""
    if zone_name not in _sim_state:
        _init_zone_state(zone_name)
    s = _sim_state[zone_name]

    t = time.time() / 45.0  # slow oscillation
    storm_pulse = max(0, math.sin(t + s["phase"])) ** 3  # occasional spikes

    s["rainfall_mm"] = max(0, s["rainfall_mm"] * 0.9 + (storm_pulse * 220) * 0.1
                            + random.uniform(-5, 5))
    s["wind_speed_kmh"] = max(0, s["wind_speed_kmh"] * 0.9 + (storm_pulse * 110) * 0.1
                               + random.uniform(-3, 3))
    s["humidity_pct"] = min(100, max(20, s["humidity_pct"] + random.uniform(-2, 2)
                                      + storm_pulse * 3))
    s["temperature_c"] = s["temperature_c"] + random.uniform(-0.4, 0.4)
    return s


# In-memory weather cache to prevent repeated blocking external API calls
_weather_cache = {}
_CACHE_TTL_SEC = 45


def get_current_weather(zone_name, lat, lon, force_refresh=False):
    """Returns dict: rainfall_mm, temperature_c, humidity_pct, wind_speed_kmh, source.
    Uses a 45-second in-memory cache to eliminate external network latency on frequent requests."""
    now = time.time()
    if not force_refresh and zone_name in _weather_cache:
        entry = _weather_cache[zone_name]
        if now - entry["timestamp"] < _CACHE_TTL_SEC:
            return entry["data"]

    result = None
    if Config.OPENWEATHER_API_KEY and requests is not None:
        try:
            resp = requests.get(
                "https://api.openweathermap.org/data/2.5/weather",
                params={
                    "lat": lat,
                    "lon": lon,
                    "appid": Config.OPENWEATHER_API_KEY,
                    "units": "metric",
                },
                timeout=4,
            )
            data = resp.json()
            rain = data.get("rain", {}).get("1h", 0) * 24  # rough hourly->daily proxy
            result = {
                "rainfall_mm": round(rain, 1),
                "temperature_c": round(data["main"]["temp"], 1),
                "humidity_pct": round(data["main"]["humidity"], 1),
                "wind_speed_kmh": round(data["wind"]["speed"] * 3.6, 1),
                "source": "openweathermap",
            }
        except Exception as exc:
            logger.warning(
                "OpenWeather call failed for %s (%s); falling back to simulation",
                zone_name, exc,
            )

    if not result:
        s = _drift(zone_name)
        result = {
            "rainfall_mm": round(s["rainfall_mm"], 1),
            "temperature_c": round(s["temperature_c"], 1),
            "humidity_pct": round(s["humidity_pct"], 1),
            "wind_speed_kmh": round(s["wind_speed_kmh"], 1),
            "source": "simulated",
        }

    _weather_cache[zone_name] = {"timestamp": now, "data": result}
    return result


def get_all_zones_weather():
    from database.db import get_zones
    return {
        zone["name"]: get_current_weather(zone["name"], zone["lat"], zone["lon"])
        for zone in get_zones()
    }
