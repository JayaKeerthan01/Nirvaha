"""
Rescue Coordination Agent

Responsibilities:
  1. Prioritize zones by combining disaster severity + population density.
  2. Recommend which rescue team(s) should deploy to which zone.
  3. Actually deploy/recall a team on request, with a real, reversible
     state change — not just a repeated suggestion.

Changelog (hardening pass):
  - `_distance_km` duplicated in hospital_agent.py — now both import
    utils.geo.haversine_km.
  - Priority scoring moved to agents/scoring.py (pure function, unit
    tested in tests/test_scoring.py).
  - Zones come from database.db.get_zones() instead of Config.ZONES.
  - Added deploy()/recall(): previously `rescue_teams.status` was seeded
    to 'available' and never changed, so "recommended deployment" was the
    same static suggestion forever, with no way to reflect a team actually
    being out in the field.
"""

from database.db import (
    query,
    get_zones,
    get_active_deployments,
    create_deployment,
    get_deployment,
    close_deployment,
    set_team_status,
)
from utils.geo import haversine_km
from agents.scoring import rescue_priority_score
from agents.hospital_agent import hospital_agent
from agents.weather_agent import weather_agent


class RescueAgent:
    name = "Rescue Agent"

    def get_all_teams(self):
        return query("SELECT * FROM rescue_teams ORDER BY team_name")

    def prioritize_zones(self, weather_assessments):
        zones_by_name = {z["name"]: z for z in get_zones()}
        priorities = []
        for w in weather_assessments:
            zone = zones_by_name.get(w["zone"])
            if not zone:
                continue
            priority_score = rescue_priority_score(w["risk_level"], zone["density"])
            priorities.append(
                {
                    "zone": w["zone"],
                    "risk_level": w["risk_level"],
                    "density": zone["density"],
                    "priority_score": round(priority_score, 3),
                }
            )
        priorities.sort(key=lambda p: p["priority_score"], reverse=True)
        return priorities

    def recommend_deployment(self, weather_assessments):
        """Suggestion only — does not change any state. See deploy() for
        actually dispatching a team."""
        priorities = self.prioritize_zones(weather_assessments)
        teams = self.get_all_teams()
        zones_by_name = {z["name"]: z for z in get_zones()}

        recommendations = []
        assigned_team_ids = set()

        for p in priorities:
            if p["risk_level"] == "Low":
                continue  # no deployment needed
            zone = zones_by_name[p["zone"]]

            # Only consider teams that are actually free right now.
            candidates = [
                t for t in teams
                if t["id"] not in assigned_team_ids and t["status"] == "available"
            ]
            if not candidates:
                break
            for t in candidates:
                t["_distance"] = haversine_km(zone["lat"], zone["lon"], t["lat"], t["lon"])
            candidates.sort(key=lambda t: t["_distance"])
            nearest = candidates[0]
            assigned_team_ids.add(nearest["id"])

            recommendations.append(
                {
                    "zone": p["zone"],
                    "priority_score": p["priority_score"],
                    "risk_level": p["risk_level"],
                    "team_id": nearest["id"],
                    "team_name": nearest["team_name"],
                    "distance_km": round(nearest["_distance"], 1),
                    "vehicles": nearest["vehicles"],
                    "ambulances": nearest["ambulances"],
                    "fire_units": nearest["fire_units"],
                    "personnel": nearest["personnel"],
                }
            )

        return recommendations

    def status_summary(self):
        teams = self.get_all_teams()
        return {
            "teams_total": len(teams),
            "teams_available": sum(1 for t in teams if t["status"] == "available"),
            "ambulances_total": sum(t["ambulances"] for t in teams),
            "fire_units_total": sum(t["fire_units"] for t in teams),
        }

    def get_active_deployments(self):
        return get_active_deployments()

    def _current_weather_by_zone(self):
        """A fresh weather read for every deploy() call, so the hospital
        pick reflects accessibility at the moment of dispatch — not a
        stale snapshot from whenever the dashboard last polled."""
        return {w["zone"]: w for w in weather_agent.assess_all_zones()}

    def deploy(self, zone, deployed_by=None):
        """Dispatches the recommended team for `zone` and reserves beds at
        the best-suited hospital. Returns the created deployment record, or
        None if no team is currently available."""
        zones_by_name = {z["name"]: z for z in get_zones()}
        zone_obj = zones_by_name.get(zone)
        if not zone_obj:
            return None

        teams = [t for t in self.get_all_teams() if t["status"] == "available"]
        if not teams:
            return None
        for t in teams:
            t["_distance"] = haversine_km(zone_obj["lat"], zone_obj["lon"], t["lat"], t["lon"])
        teams.sort(key=lambda t: t["_distance"])
        team = teams[0]

        hospitals = hospital_agent.recommend_for_zone(zone_obj, top_n=1, weather_by_zone=self._current_weather_by_zone())
        hospital = hospitals[0] if hospitals else None

        beds_reserved = 0
        hospital_id = None
        if hospital:
            hospital_id = hospital["id"]
            beds_reserved = hospital_agent.reserve_for_deployment(hospital_id)

        set_team_status(team["id"], "deployed")
        deployment_id = create_deployment(
            team_id=team["id"],
            zone=zone,
            hospital_id=hospital_id,
            beds_reserved=beds_reserved,
            deployed_by=deployed_by,
        )
        return get_deployment(deployment_id)

    def recall(self, deployment_id):
        """Reverses deploy(): frees the team and returns reserved beds."""
        deployment = get_deployment(deployment_id)
        if not deployment or deployment["status"] != "active":
            return None

        set_team_status(deployment["team_id"], "available")
        if deployment["hospital_id"] and deployment["beds_reserved"]:
            hospital_agent.release_beds(deployment["hospital_id"], deployment["beds_reserved"])
        close_deployment(deployment_id)
        return deployment


rescue_agent = RescueAgent()
