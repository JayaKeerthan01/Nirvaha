"""
IoT Telemetry & Environmental Sensor Agent

Responsibilities:
  1. Monitors physical IoT telemetry stations (lake flood depth gauges, river crest sensors, rain gauges).
  2. Evaluates live sensor readings against safety warning and critical emergency thresholds.
  3. Provides structured telemetry data for the interactive command map.
  4. Ingests field telemetry packets from microcontrollers (ESP32 / Arduino / MQTT gateways).
"""

import logging
from database.db import get_all_iot_sensors, update_iot_sensor_reading

logger = logging.getLogger(__name__)


class IoTAgent:
    name = "IoT Environmental Sensor Agent"

    def get_all_sensors_with_status(self):
        """Returns all physical IoT sensors annotated with threshold status and risk factors."""
        sensors = get_all_iot_sensors()
        enriched = []
        for s in sensors:
            cur_val = s["current_value"]
            crit_val = s["critical_threshold"]
            warn_val = s["warning_threshold"]

            # Ratio of current level to critical threshold
            capacity_ratio = round((cur_val / crit_val) * 100, 1) if crit_val > 0 else 0

            status = "Normal"
            if cur_val >= crit_val:
                status = "Critical"
            elif cur_val >= warn_val:
                status = "Warning"

            enriched.append({
                **s,
                "status": status,
                "fill_percentage": min(capacity_ratio, 100),
                "is_critical": status == "Critical",
                "is_warning": status == "Warning",
            })
        return enriched

    def ingest_telemetry(self, sensor_id: str, reading: float, battery_pct: int = None):
        """Ingests live telemetry from an IoT device."""
        return update_iot_sensor_reading(sensor_id, reading, battery_pct)


iot_agent = IoTAgent()
