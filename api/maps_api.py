"""
Mapping / routing data source.

The dashboard renders maps client-side with Leaflet.js + OpenStreetMap tiles
(free, no API key required). This module supplies the *data* Leaflet draws:
zone markers, hospital/rescue-team positions, and evacuation route polylines.

If Config.GOOGLE_MAPS_API_KEY is set, `get_directions()` will call the real
Google Directions API instead of the built-in synthetic route generator —
useful once you're ready to go live with turn-by-turn routing.
"""

import logging
import math
import random

from config import Config

logger = logging.getLogger(__name__)

try:
    import requests
except ImportError:
    requests = None


CONGESTION_MULTIPLIERS = {
    "Low": 1.0,
    "Moderate": 1.35,
    "High": 2.1,
    "Severe": 3.6,
}


def get_zone_markers():
    from database.db import get_zones
    return [
        {"name": z["name"], "lat": z["lat"], "lon": z["lon"], "density": z["density"]}
        for z in get_zones()
    ]


def _coord(point):
    """Normalize input point (dict or tuple/list) into (lat, lon)."""
    if isinstance(point, dict):
        return (float(point["lat"]), float(point["lon"]))
    return (float(point[0]), float(point[1]))


def _synthetic_route(start, end, n_points=8, avoid_points=None):
    """Builds a plausible curved polyline between two points, avoiding hazard points
    if specified, so the map has realistic waypoints to draw."""
    lat1, lon1 = _coord(start)
    lat2, lon2 = _coord(end)
    points = []
    rng = random.Random(f"{lat1:.4f}{lon1:.4f}{lat2:.4f}{lon2:.4f}")

    # Vector from start to end
    dlat = lat2 - lat1
    dlon = lon2 - lon1

    # Perpendicular normal vector (-dlon, dlat)
    norm_lat = -dlon
    norm_lon = dlat
    norm_len = math.hypot(norm_lat, norm_lon) or 1.0
    norm_lat /= norm_len
    norm_lon /= norm_len

    # If any avoid_points exist, deflect away from them
    deflection_bias = 0.0
    if avoid_points:
        for ap in avoid_points:
            alat, alon = _coord(ap)
            # Distance from midpoint to avoid point
            mid_lat = (lat1 + lat2) / 2.0
            mid_lon = (lon1 + lon2) / 2.0
            dist_to_hazard = math.hypot(mid_lat - alat, mid_lon - alon)
            if dist_to_hazard < 0.08:  # within ~9km
                deflection_bias += 0.025 if rng.random() > 0.5 else -0.025

    for i in range(n_points + 1):
        f = i / n_points
        base_lat = lat1 + dlat * f
        base_lon = lon1 + dlon * f

        # Parabolic curve + small realistic road jitter + hazard deflection
        curve = math.sin(f * math.pi) * (rng.uniform(-0.008, 0.008) + deflection_bias)
        lat = base_lat + norm_lat * curve
        lon = base_lon + norm_lon * curve
        points.append([round(lat, 5), round(lon, 5)])

    return points


def _decode_polyline(encoded):
    """Decodes Google's encoded polyline format into [[lat, lon], ...]."""
    points = []
    index = lat = lon = 0
    while index < len(encoded):
        for coord in ("lat", "lon"):
            shift = result = 0
            while True:
                b = ord(encoded[index]) - 63
                index += 1
                result |= (b & 0x1f) << shift
                shift += 5
                if b < 0x20:
                    break
            delta = ~(result >> 1) if result & 1 else (result >> 1)
            if coord == "lat":
                lat += delta
            else:
                lon += delta
        points.append([lat / 1e5, lon / 1e5])
    return points


def get_directions(start_zone, end_zone, congestion_level="Low", avoid_zones=None):
    """Returns dict: {distance_km, duration_min, path: [[lat, lon], ...], congestion_level, source}"""
    start = _coord(start_zone)
    end = _coord(end_zone)

    if Config.GOOGLE_MAPS_API_KEY and requests is not None:
        try:
            resp = requests.get(
                "https://maps.googleapis.com/maps/api/directions/json",
                params={
                    "origin": f"{start[0]},{start[1]}",
                    "destination": f"{end[0]},{end[1]}",
                    "key": Config.GOOGLE_MAPS_API_KEY,
                },
                timeout=5,
            )
            data = resp.json()
            leg = data["routes"][0]["legs"][0]
            base_dur = leg["duration"]["value"] / 60
            multiplier = CONGESTION_MULTIPLIERS.get(congestion_level, 1.0)
            return {
                "distance_km": round(leg["distance"]["value"] / 1000, 1),
                "duration_min": round(base_dur * multiplier, 1),
                "path": _decode_polyline(data["routes"][0]["overview_polyline"]["points"]),
                "congestion_level": congestion_level,
                "source": "google_maps",
            }
        except Exception as exc:
            logger.warning(
                "Google Directions call failed (%s); falling back to simulated route", exc,
            )

    # Haversine distance as a stand-in for real routing distance
    from utils.geo import haversine_km
    distance_km = haversine_km(*start, *end) * 1.35  # *1.35 road-vs-straight-line factor
    multiplier = CONGESTION_MULTIPLIERS.get(congestion_level, 1.0)
    base_duration = distance_km * 2.1  # ~28 km/h baseline urban speed
    duration_min = round(base_duration * multiplier, 1)

    avoid_points = [_coord(z) for z in (avoid_zones or [])]
    return {
        "distance_km": round(distance_km, 1),
        "duration_min": max(1.0, duration_min),
        "path": _synthetic_route(start, end, n_points=8, avoid_points=avoid_points),
        "congestion_level": congestion_level,
        "source": "simulated",
    }


def get_shelter_directions(start_point, shelter, congestion_level="Low", avoid_zones=None):
    """Convenience helper to route from a zone/coordinate to an evacuation relief shelter."""
    return get_directions(start_point, shelter, congestion_level=congestion_level, avoid_zones=avoid_zones)
