"""
Hospital and Rescue Auto-Assignment & Routing Engine

Responsibilities:
  1. Auto-route closest, most accessible hospital for each disaster zone.
  2. Auto-assign nearest rescue unit by geographic proximity.
  3. Provide full route polylines and ETAs for both hospital and rescue dispatch.
  4. Support autonomous background execution or 1-click batch assignment.
"""

import logging
from database.db import (
    get_zones,
    log_alert,
    is_auto_dispatch_enabled,
    get_active_deployments,
)
from utils.geo import haversine_km
from agents.hospital_agent import hospital_agent
from agents.rescue_agent import rescue_agent
from agents.weather_agent import weather_agent
from api import maps_api

logger = logging.getLogger(__name__)


class AutoAssignmentEngine:
    name = "Auto-Assignment Engine"

    def compute_all_zone_assignments(self, weather_by_zone=None):
        """Computes optimal hospital route and nearest rescue team for EVERY zone."""
        zones = get_zones()
        if not weather_by_zone:
            weather_assessments = weather_agent.assess_all_zones()
            weather_by_zone = {w["zone"]: w for w in weather_assessments}

        active_deployments = {d["zone"]: d for d in get_active_deployments()}
        all_teams = rescue_agent.get_all_teams()
        available_teams = [t for t in all_teams if t["status"] == "available"]
        assigned_team_ids = set()

        # Sort zones by risk severity and population density priority
        priorities = rescue_agent.prioritize_zones(list(weather_by_zone.values()))
        priority_map = {p["zone"]: p for p in priorities}

        assignments = {}

        for zone in zones:
            zname = zone["name"]
            w = weather_by_zone.get(zname, {})
            risk_level = w.get("risk_level", "Low")
            p_info = priority_map.get(zname, {"priority_score": 0.1})

            # 1. AUTO-ROUTE CLOSEST ACCESSIBLE HOSPITAL
            # Evaluates capacity + geographic distance + destination hazard risk
            hospitals = hospital_agent.recommend_for_zone(zone, top_n=3, weather_by_zone=weather_by_zone)
            best_hospital = hospitals[0] if hospitals else None
            hospital_route = None

            if best_hospital:
                start_obj = {"lat": zone["lat"], "lon": zone["lon"]}
                end_obj = {"lat": best_hospital["lat"], "lon": best_hospital["lon"]}
                route_data = maps_api.get_directions(start_obj, end_obj)
                hospital_route = {
                    "hospital_id": best_hospital["id"],
                    "hospital_name": best_hospital["hospital_name"],
                    "beds_available": best_hospital["beds_available"],
                    "icu_available": best_hospital["icu_available"],
                    "doctors_available": best_hospital["doctors_available"],
                    "contact": best_hospital.get("contact", "108"),
                    "accessibility_status": best_hospital.get("accessibility_status", "Operational"),
                    "distance_km": route_data.get("distance_km", best_hospital["distance_km"]),
                    "duration_min": route_data.get("duration_min", round(best_hospital["distance_km"] * 2.5, 1)),
                    "path": route_data.get("path", []),
                }

            # 2. AUTO-ASSIGN NEAREST RESCUE UNIT BY GEOGRAPHY
            active_dep = active_deployments.get(zname)
            assigned_team = None
            rescue_route = None

            if active_dep:
                # Already deployed
                team_obj = next((t for t in all_teams if t["id"] == active_dep["team_id"]), None)
                assigned_team = {
                    "team_id": active_dep["team_id"],
                    "team_name": team_obj["team_name"] if team_obj else f"Team #{active_dep['team_id']}",
                    "status": "deployed",
                    "deployed_at": active_dep["deployed_at"],
                    "deployed_by": active_dep["deployed_by"],
                    "deployment_id": active_dep["id"],
                }
            else:
                # Find closest free unit by Haversine distance
                candidates = [t for t in available_teams if t["id"] not in assigned_team_ids]
                if candidates:
                    for t in candidates:
                        t["_calc_dist"] = haversine_km(zone["lat"], zone["lon"], t["lat"], t["lon"])
                    candidates.sort(key=lambda t: t["_calc_dist"])
                    nearest = candidates[0]
                    assigned_team_ids.add(nearest["id"])

                    start_team = {"lat": nearest["lat"], "lon": nearest["lon"]}
                    end_zone = {"lat": zone["lat"], "lon": zone["lon"]}
                    r_data = maps_api.get_directions(start_team, end_zone)

                    assigned_team = {
                        "team_id": nearest["id"],
                        "team_name": nearest["team_name"],
                        "status": "recommended",
                        "distance_km": r_data.get("distance_km", round(nearest["_calc_dist"], 1)),
                        "duration_min": r_data.get("duration_min", round(nearest["_calc_dist"] * 2.2, 1)),
                        "vehicles": nearest["vehicles"],
                        "ambulances": nearest["ambulances"],
                        "fire_units": nearest["fire_units"],
                        "personnel": nearest["personnel"],
                    }
                    rescue_route = {
                        "from_team": nearest["team_name"],
                        "to_zone": zname,
                        "path": r_data.get("path", []),
                        "duration_min": assigned_team["duration_min"],
                        "distance_km": assigned_team["distance_km"],
                    }

            assignments[zname] = {
                "zone": zname,
                "lat": zone["lat"],
                "lon": zone["lon"],
                "density": zone["density"],
                "risk_level": risk_level,
                "priority_score": p_info.get("priority_score", 0.0),
                "is_active_deployment": bool(active_dep),
                "hospital": hospital_route,
                "rescue_team": assigned_team,
                "rescue_route": rescue_route,
            }

        return assignments

    def execute_assignment(self, zone_name, user_name="Auto-Assignment Engine"):
        """Dispatches the assigned team for a single zone and reserves hospital beds."""
        deployment = rescue_agent.deploy(zone_name, deployed_by=user_name)
        if deployment:
            log_alert(
                title="Automated Unit Dispatch",
                message=f"Team #{deployment['team_id']} auto-assigned to {zone_name} (operator: {user_name}).",
                severity="Medium",
                zone=zone_name,
            )
        return deployment

    def execute_all_assignments(self, user_name="Auto-Dispatch All"):
        """Batch auto-assigns and dispatches nearest available teams to all High/Medium risk zones."""
        weather_assessments = weather_agent.assess_all_zones()
        weather_by_zone = {w["zone"]: w for w in weather_assessments}
        assignments = self.compute_all_zone_assignments(weather_by_zone)

        dispatched = []
        for zname, item in assignments.items():
            if item["risk_level"] in ("High", "Medium") and not item["is_active_deployment"]:
                if item["rescue_team"] and item["rescue_team"].get("status") == "recommended":
                    dep = self.execute_assignment(zname, user_name=user_name)
                    if dep:
                        dispatched.append({"zone": zname, "deployment": dict(dep)})

        return {
            "dispatched_count": len(dispatched),
            "dispatched": dispatched,
            "auto_dispatch_active": is_auto_dispatch_enabled(),
        }


auto_assignment_engine = AutoAssignmentEngine()
