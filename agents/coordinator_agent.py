"""
Coordinator Agent

The central intelligence of the system. Calls the four specialized agents,
combines their outputs, and produces one comprehensive response-strategy
payload for the dashboard.
"""

from agents.weather_agent import weather_agent
from agents.traffic_agent import traffic_agent
from agents.hospital_agent import hospital_agent
from agents.rescue_agent import rescue_agent
from agents.auto_assignment_engine import auto_assignment_engine
from agents.satellite_agent import satellite_agent
from agents.iot_agent import iot_agent
from agents.social_media_agent import social_media_agent
from database.db import get_zones, query, is_auto_dispatch_enabled, get_emergency_reports

RISK_ORDER = {"Low": 0, "Medium": 1, "High": 2}


class CoordinatorAgent:
    name = "Coordinator Agent"

    def full_report(self):
        weather_assessments = weather_agent.assess_all_zones()
        weather_by_zone = {w["zone"]: w for w in weather_assessments}
        traffic_assessments = traffic_agent.assess_all_zones(weather_by_zone)

        hospital_summary = hospital_agent.status_summary()
        rescue_summary = rescue_agent.status_summary()
        deployment = rescue_agent.recommend_deployment(weather_assessments)
        zone_priorities = rescue_agent.prioritize_zones(weather_assessments)
        active_deployments = rescue_agent.get_active_deployments()

        # Multi-zone auto-assignment engine: nearest rescue unit & closest hospital for EVERY zone
        zone_assignments = auto_assignment_engine.compute_all_zone_assignments(weather_by_zone)

        # All hospitals with accessibility status (for the unified map)
        all_hospitals = hospital_agent.get_all_hospitals_with_status(weather_by_zone)

        # All rescue teams (for the unified map)
        all_rescue_teams = rescue_agent.get_all_teams()

        # Zones lookup for coordinates
        zones_list = get_zones()
        zone_coords = {z["name"]: (z["lat"], z["lon"]) for z in zones_list}

        # Recent alerts with coordinates for map beacon visualization
        raw_alerts = query("SELECT * FROM alerts ORDER BY id DESC LIMIT 15")
        active_alerts = []
        for a in raw_alerts:
            a_dict = dict(a)
            if a_dict.get("zone") in zone_coords:
                a_dict["lat"], a_dict["lon"] = zone_coords[a_dict["zone"]]
                active_alerts.append(a_dict)

        # Physical IoT sensors with threshold analysis
        iot_sensors = iot_agent.get_all_sensors_with_status()

        # Satellite & Doppler radar live state
        satellite_overview = satellite_agent.get_satellite_overview(zones_list)

        # Crowdsourced SOS distress messages
        social_distress = social_media_agent.get_feed(limit=25)

        # Citizen emergency incident reports
        emergency_reports = get_emergency_reports(limit=25)

        overall_risk = "Low"
        for w in weather_assessments:
            if RISK_ORDER[w["risk_level"]] > RISK_ORDER[overall_risk]:
                overall_risk = w["risk_level"]

        top_zone_name = zone_priorities[0]["zone"] if zone_priorities else None
        top_zone = next((z for z in zones_list if z["name"] == top_zone_name), None)
        recommended_hospitals = hospital_agent.recommend_for_zone(top_zone, weather_by_zone=weather_by_zone) if top_zone else []

        recommended_route = None
        if top_zone:
            recommended_route = traffic_agent.safest_route(top_zone_name, traffic_assessments)

        return {
            "overall_risk": overall_risk,
            "weather": weather_assessments,
            "traffic": traffic_assessments,
            "hospital_summary": hospital_summary,
            "rescue_summary": rescue_summary,
            "zone_priorities": zone_priorities,
            "deployment_recommendations": deployment,
            "active_deployments": active_deployments,
            "top_priority_zone": top_zone_name,
            "recommended_hospitals": recommended_hospitals,
            "recommended_route": recommended_route,
            "zone_assignments": zone_assignments,
            "all_hospitals": all_hospitals,
            "all_rescue_teams": all_rescue_teams,
            "active_alerts": active_alerts,
            "auto_dispatch_active": is_auto_dispatch_enabled(),
            "iot_sensors": iot_sensors,
            "satellite": satellite_overview,
            "social_distress": social_distress,
            "emergency_reports": emergency_reports,
        }


coordinator_agent = CoordinatorAgent()

