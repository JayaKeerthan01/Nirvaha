"""
Hospital Resource Agent

Responsibilities:
  1. Monitor bed / doctor / ambulance / ICU availability at every hospital.
  2. Recommend the best hospital(s) for a given affected zone, weighing
     capacity, distance, AND whether the hospital's own location is
     currently safe to reach.
  3. Reserve/release beds when a deployment is actually dispatched/recalled,
     so capacity numbers reflect real state instead of a static suggestion.

Changelog (hardening pass):
  - `_distance_km` used to be duplicated here and in rescue_agent.py — now
    both import utils.geo.haversine_km.
  - Suitability scoring moved to agents/scoring.py as pure functions so it
    can be unit tested without a live DB (see tests/test_scoring.py).
  - beds_available previously never changed after a deployment — added
    reserve_for_deployment()/release_beds() called from the /api/deploy and
    /api/recall routes.
  - recommend_for_zone() previously scored purely on capacity + distance,
    with no concept of a hospital's own location being unsafe. A disaster
    doesn't stop at the affected zone's border — a hospital sitting inside
    a High-risk flood/cyclone/landslide zone can be just as cut off as the
    people it's meant to treat. Added an accessibility penalty + status
    (agents/scoring.py::hospital_accessibility_penalty/_status) computed
    from that hospital's OWN zone risk, not the requesting zone's.
"""

from config import Config
from database.db import query, adjust_hospital_beds
from utils.geo import haversine_km
from agents.scoring import (
    hospital_capacity_score,
    hospital_suitability,
    hospital_accessibility_penalty,
    hospital_accessibility_status,
)


class HospitalAgent:
    name = "Hospital Agent"

    def get_all_hospitals(self):
        return query("SELECT * FROM hospitals ORDER BY hospital_name")

    def get_all_hospitals_with_status(self, weather_by_zone=None):
        """Same as get_all_hospitals(), but each row also gets
        accessibility_status/accessibility_risk based on the CURRENT risk
        of the hospital's own zone — not any particular requesting zone.
        Used by /api/hospitals so both the ops hospitals page and the
        citizen hospitals page show this without needing a "from" zone."""
        weather_by_zone = weather_by_zone or {}
        hospitals = self.get_all_hospitals()
        for h in hospitals:
            w = weather_by_zone.get(h["location"])
            risk = w["risk_level"] if w else "Low"
            h["accessibility_status"] = hospital_accessibility_status(risk)
            h["accessibility_risk"] = risk
        return hospitals

    def recommend_for_zone(self, zone, top_n=3, weather_by_zone=None):
        """weather_by_zone (zone name -> weather_agent assessment) is
        optional so existing callers keep working, but every current
        caller passes it — see coordinator_agent, rescue_agent.deploy(),
        and citizen_chat_agent. Without it, accessibility just can't be
        assessed and every hospital is treated as Operational."""
        weather_by_zone = weather_by_zone or {}
        hospitals = self.get_all_hospitals()
        scored = []
        for h in hospitals:
            distance = haversine_km(zone["lat"], zone["lon"], h["lat"], h["lon"])
            capacity_score = hospital_capacity_score(
                h["beds_available"], h["beds_total"], h["icu_available"], h["doctors_available"]
            )
            suitability = hospital_suitability(capacity_score, distance)

            hospital_zone_weather = weather_by_zone.get(h["location"])
            hospital_zone_risk = hospital_zone_weather["risk_level"] if hospital_zone_weather else "Low"
            penalty = hospital_accessibility_penalty(hospital_zone_risk)
            suitability -= penalty

            scored.append({
                **h,
                "distance_km": round(distance, 1),
                "suitability": round(suitability, 1),
                "accessibility_status": hospital_accessibility_status(hospital_zone_risk),
                "accessibility_risk": hospital_zone_risk,
            })

        scored.sort(key=lambda h: h["suitability"], reverse=True)
        return scored[:top_n]

    def status_summary(self):
        hospitals = self.get_all_hospitals()
        total_beds = sum(h["beds_total"] for h in hospitals)
        available_beds = sum(h["beds_available"] for h in hospitals)
        return {
            "hospitals_online": len(hospitals),
            "total_beds": total_beds,
            "beds_available": available_beds,
            "occupancy_pct": round(100 * (1 - available_beds / max(total_beds, 1)), 1),
        }

    def reserve_for_deployment(self, hospital_id, beds=None):
        """Called when a deployment is dispatched — reserves an estimated
        number of beds at the chosen hospital so capacity numbers stay
        honest until the deployment is recalled."""
        beds = beds if beds is not None else Config.BEDS_RESERVED_PER_DEPLOYMENT
        adjust_hospital_beds(hospital_id, -beds)
        return beds

    def release_beds(self, hospital_id, beds):
        adjust_hospital_beds(hospital_id, beds)


hospital_agent = HospitalAgent()
