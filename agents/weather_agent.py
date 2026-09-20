"""
Weather Prediction Agent

Responsibilities:
  1. Pull current meteorological readings for every monitored zone.
  2. Run the disaster-prediction ML model over those readings.
  3. Return an early-warning risk assessment per zone.
  4. Log every assessment to the `disasters` history table and raise an
     alert exactly once per transition into High risk.

Changelog (hardening pass):
  - `_last_alerted_level` used to be a plain module-level dict. That state
    is now in database.db.zone_state, so it survives restarts and stays
    consistent if this ever runs under multiple worker processes.
  - Every assessment is now written to the `disasters` table via
    log_disaster_event — previously that table existed in the schema but
    nothing ever wrote to it.
  - Zones come from database.db.get_zones() instead of a hardcoded
    Config.ZONES list, so /admin/zones changes take effect immediately.
"""

from api import weather_api
from ml import disaster_prediction
from database.db import log_alert, get_zone_state, set_zone_state, log_disaster_event, get_zones


class WeatherAgent:
    name = "Weather Agent"

    def assess_zone(self, zone):
        weather = weather_api.get_current_weather(zone["name"], zone["lat"], zone["lon"])
        result = disaster_prediction.predict(
            weather["rainfall_mm"],
            weather["temperature_c"],
            weather["humidity_pct"],
            weather["wind_speed_kmh"],
        )

        assessment = {
            "zone": zone["name"],
            "lat": zone["lat"],
            "lon": zone["lon"],
            "weather": weather,
            "prediction": result["prediction"],
            "probabilities": result["probabilities"],
            "risk_level": result["risk_level"],
            "risk_score": result["risk_score"],
        }

        log_disaster_event(
            location=zone["name"],
            disaster_type=result["prediction"],
            severity=result["risk_level"],
            probability=result["risk_score"],
            prediction=result["prediction"],
            lat=zone["lat"],
            lon=zone["lon"],
        )
        self._maybe_alert(zone["name"], assessment)
        return assessment

    def _maybe_alert(self, zone_name, assessment):
        prev = get_zone_state(zone_name)
        current = assessment["risk_level"]
        set_zone_state(zone_name, current)

        if current == "High" and prev != "High":
            log_alert(
                title=f"{assessment['prediction']} risk elevated to HIGH",
                message=(
                    f"{zone_name} is showing a high probability of "
                    f"{assessment['prediction']} (score {assessment['risk_score']})."
                ),
                severity="High",
                zone=zone_name,
            )
            # 1. Multi-channel real-time notifications (Push, Email, SMS)
            try:
                from api.notification_service import notification_service
                notification_service.dispatch_high_risk_alert(zone_name, assessment)
            except Exception as e:
                import logging
                logging.getLogger(__name__).warning("Failed to dispatch alert notifications: %s", e)

            # 2. Autonomous rescue & hospital auto-dispatch if enabled
            try:
                from database.db import is_auto_dispatch_enabled
                if is_auto_dispatch_enabled():
                    from agents.rescue_agent import rescue_agent
                    rescue_agent.deploy(
                        zone_name,
                        deployed_by="Autonomous Dispatch",
                        weather_by_zone={zone_name: assessment},
                    )
            except Exception as e:
                import logging
                logging.getLogger(__name__).warning("Failed to execute autonomous assignment: %s", e)

    def assess_all_zones(self):
        return [self.assess_zone(z) for z in get_zones()]


weather_agent = WeatherAgent()
