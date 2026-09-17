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
from database.db import get_zones

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

        overall_risk = "Low"
        for w in weather_assessments:
            if RISK_ORDER[w["risk_level"]] > RISK_ORDER[overall_risk]:
                overall_risk = w["risk_level"]

        # Build a hospital recommendation for the single highest-priority zone
        top_zone_name = zone_priorities[0]["zone"] if zone_priorities else None
        top_zone = next((z for z in get_zones() if z["name"] == top_zone_name), None)
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
        }


coordinator_agent = CoordinatorAgent()
