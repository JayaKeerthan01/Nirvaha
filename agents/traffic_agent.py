"""
Traffic Analysis Agent

Responsibilities:
  1. Predict congestion level for every zone using the traffic ML model.
  2. Flag roads as blocked/high-risk when a zone's disaster risk is High.
  3. Recommend the safest evacuation route between an affected zone and the
     nearest zone that is currently low-risk.
"""

from datetime import datetime

from api import maps_api
from ml import traffic_prediction
from database.db import get_zones

CONGESTION_TO_ROAD_STATUS = {
    "Low": "Clear",
    "Moderate": "Slow-moving",
    "High": "Heavily congested",
    "Severe": "Blocked / impassable",
}


class TrafficAgent:
    name = "Traffic Agent"

    def assess_zone(self, zone, weather_condition="clear"):
        hour = datetime.now().hour
        result = traffic_prediction.predict(hour, zone["name"], weather_condition)
        road_status = CONGESTION_TO_ROAD_STATUS[result["congestion_level"]]

        return {
            "zone": zone["name"],
            "lat": zone["lat"],
            "lon": zone["lon"],
            "congestion_level": result["congestion_level"],
            "confidence": result["confidence"],
            "road_status": road_status,
            "blocked": result["congestion_level"] == "Severe",
        }

    def assess_all_zones(self, weather_by_zone=None):
        weather_by_zone = weather_by_zone or {}
        assessments = []
        for zone in get_zones():
            w = weather_by_zone.get(zone["name"])
            condition = "clear"
            if w:
                if w.get("risk_level") == "High":
                    condition = "flood_alert"
                elif w.get("weather", {}).get("rainfall_mm", 0) > 80:
                    condition = "storm"
                elif w.get("weather", {}).get("rainfall_mm", 0) > 20:
                    condition = "rain"
            assessments.append(self.assess_zone(zone, condition))
        return assessments

    def safest_route(self, from_zone_name, traffic_assessments, risk_by_zone=None):
        """Pick the safest and least-congested target zone, steering around hazard hot-spots."""
        from database.db import get_zones
        zones_by_name = {z["name"]: z for z in get_zones()}
        from_zone = zones_by_name.get(from_zone_name)
        if not from_zone:
            return None

        risk_by_zone = risk_by_zone or {}
        candidates = [a for a in traffic_assessments if a["zone"] != from_zone_name]
        if not candidates:
            return None

        congestion_rank = {"Low": 0, "Moderate": 12, "High": 35, "Severe": 90}
        risk_rank = {"Low": 0, "Moderate": 25, "High": 200}

        from utils.geo import haversine_km

        def candidate_score(c):
            c_zone = c["zone"]
            r_level = risk_by_zone.get(c_zone, {}).get("risk_level", "Low") if isinstance(risk_by_zone.get(c_zone), dict) else risk_by_zone.get(c_zone, "Low")
            cong_score = congestion_rank.get(c.get("congestion_level", "Low"), 0)
            risk_score = risk_rank.get(r_level, 0)
            target_obj = zones_by_name[c_zone]
            dist = haversine_km(from_zone["lat"], from_zone["lon"], target_obj["lat"], target_obj["lon"])
            return risk_score + cong_score + (dist * 1.5)

        candidates.sort(key=candidate_score)
        best = candidates[0]
        destination_zone = zones_by_name[best["zone"]]

        # Avoid zones with High risk in path generation
        avoid_zones = [
            zones_by_name[z_name]
            for z_name, r_data in risk_by_zone.items()
            if (r_data.get("risk_level") if isinstance(r_data, dict) else r_data) == "High"
            and z_name in zones_by_name
            and z_name != from_zone_name
            and z_name != best["zone"]
        ]

        route = maps_api.get_directions(
            from_zone,
            destination_zone,
            congestion_level=best.get("congestion_level", "Low"),
            avoid_zones=avoid_zones,
        )
        route["from"] = from_zone_name
        route["to"] = best["zone"]
        route["from_coords"] = [from_zone["lat"], from_zone["lon"]]
        route["to_coords"] = [destination_zone["lat"], destination_zone["lon"]]
        route["hazard_avoidance"] = len(avoid_zones) > 0
        return route

    def safe_shelter_route(self, from_zone_name, risk_by_zone=None, traffic_assessments=None):
        """Find the nearest relief shelter with available capacity, avoiding hazard zones."""
        from database.db import get_zones, get_shelters
        all_zones = get_zones()
        if not all_zones:
            return None

        clean_name = (from_zone_name or "").strip().lower()
        zones_by_name = {z["name"].lower(): z for z in all_zones}
        from_zone = zones_by_name.get(clean_name)

        shelters = get_shelters()
        if not shelters:
            return None

        if not from_zone:
            # Check if any shelter matches this zone name to borrow coordinates
            matching_shelter = [s for s in shelters if s["zone"].lower() == clean_name]
            if matching_shelter:
                from_zone = {"name": from_zone_name, "lat": matching_shelter[0]["lat"], "lon": matching_shelter[0]["lon"]}
            else:
                from_zone = all_zones[0]

        risk_by_zone = risk_by_zone or {}
        traffic_by_zone = {a["zone"]: a.get("congestion_level", "Low") for a in (traffic_assessments or [])}

        from utils.geo import haversine_km

        scored = []
        for s in shelters:
            dist = haversine_km(from_zone["lat"], from_zone["lon"], s["lat"], s["lon"])
            s_zone = s["zone"]
            r_level = risk_by_zone.get(s_zone, {}).get("risk_level", "Low") if isinstance(risk_by_zone.get(s_zone), dict) else risk_by_zone.get(s_zone, "Low")
            
            # Penalize high risk shelters and full capacity shelters
            risk_pen = 300 if r_level == "High" else (40 if r_level == "Moderate" else 0)
            occ_ratio = (s["current_occupancy"] / max(1, s["capacity"]))
            capacity_pen = 150 if occ_ratio >= 1.0 else (occ_ratio * 30)
            cong_pen = {"Low": 0, "Moderate": 10, "High": 25, "Severe": 60}.get(traffic_by_zone.get(s_zone, "Low"), 0)

            total_penalty = dist + risk_pen + capacity_pen + cong_pen
            scored.append((total_penalty, s))

        scored.sort(key=lambda x: x[0])
        best_shelter = scored[0][1]

        # Avoid zones with High risk
        avoid_zones = [
            zones_by_name[z_name]
            for z_name, r_data in risk_by_zone.items()
            if (r_data.get("risk_level") if isinstance(r_data, dict) else r_data) == "High"
            and z_name in zones_by_name
            and z_name != from_zone_name
        ]

        cong = traffic_by_zone.get(best_shelter["zone"], "Low")
        route = maps_api.get_shelter_directions(from_zone, dict(best_shelter), congestion_level=cong, avoid_zones=avoid_zones)

        return {
            "shelter": dict(best_shelter),
            "from": from_zone_name,
            "from_coords": [from_zone["lat"], from_zone["lon"]],
            "shelter_coords": [best_shelter["lat"], best_shelter["lon"]],
            "to_shelter": best_shelter["name"],
            "distance_km": route["distance_km"],
            "duration_min": route["duration_min"],
            "path": route["path"],
            "congestion_level": route.get("congestion_level", "Low"),
            "hazard_avoidance": len(avoid_zones) > 0,
        }


traffic_agent = TrafficAgent()

