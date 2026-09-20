"""
Comprehensive Verification Test Suite for Nirvaha System Architecture Data Sources

Tests all 8 architectural data sources:
  1. Weather Data (Live meteorological API & simulation)
  2. Satellite Images (Live Doppler radar & satellite infrared overlays)
  3. IoT Sensors (Physical lake/flood depth gauges & telemetry ingestion)
  4. Traffic Sensors (Urban road congestion, speed & blockage status)
  5. Hospital Databases (Live bed availability & accessibility tracking)
  6. Social Media (NLP crowdsourced SOS distress intelligence: Twitter/Telegram/WhatsApp)
  7. Emergency Reports (Citizen incident submission & dispatcher queue)
  8. Historical Data (Longitudinal disaster event log & analytics)
"""

import unittest
from app import app
from database.db import (
    init_db,
    get_all_iot_sensors,
    update_iot_sensor_reading,
    get_social_distress_feed,
    log_social_distress,
    create_emergency_report,
    get_emergency_reports,
    update_emergency_report_status,
)
from agents.satellite_agent import satellite_agent
from agents.iot_agent import iot_agent
from agents.social_media_agent import social_media_agent
from agents.weather_agent import weather_agent
from agents.traffic_agent import traffic_agent
from agents.hospital_agent import hospital_agent
from agents.coordinator_agent import coordinator_agent


class TestDataSources(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        init_db(seed=True)
        cls.client = app.test_client()

    def setUp(self):
        with self.client.session_transaction() as sess:
            sess["user_id"] = 1
            sess["role"] = "admin"
            sess["user_name"] = "Duty Officer"
            sess["csrf_token"] = "test-csrf-token"
        self.headers = {"X-CSRFToken": "test-csrf-token"}

    # 1. Weather Data
    def test_01_weather_data_source(self):
        assessments = weather_agent.assess_all_zones()
        self.assertGreater(len(assessments), 0)
        first = assessments[0]
        self.assertIn("zone", first)
        self.assertIn("risk_level", first)
        self.assertIn("weather", first)
        self.assertIn("rainfall_mm", first["weather"])
        self.assertIn("wind_speed_kmh", first["weather"])

    # 2. Satellite Images & Doppler Radar
    def test_02_satellite_images_source(self):
        meta = satellite_agent.fetch_latest_radar_metadata()
        self.assertIn("radar_tile_template", meta)
        self.assertIn("satellite_infrared_template", meta)
        self.assertTrue(meta["radar_tile_template"].startswith("http"))

        overview = satellite_agent.get_satellite_overview([{"name": "Koramangala", "lat": 12.9352, "lon": 77.6245}])
        self.assertEqual(overview["status"], "Online")
        self.assertIn("radar_tile_url", overview)
        self.assertGreater(len(overview["storm_cells"]), 0)

        # Endpoint test
        resp = self.client.get("/api/satellite/live")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertIn("radar_tile_url", data)

    # 3. IoT Sensors & Physical Gauges
    def test_03_iot_sensors_source(self):
        sensors = iot_agent.get_all_sensors_with_status()
        self.assertGreaterEqual(len(sensors), 5)
        gauge = sensors[0]
        self.assertIn("current_value", gauge)
        self.assertIn("critical_threshold", gauge)
        self.assertIn("status", gauge)
        self.assertIn("fill_percentage", gauge)

        # Ingestion test via API
        resp = self.client.post(
            "/api/iot/telemetry",
            json={
                "sensor_id": "SENSOR-HSR-AGARA-01",
                "reading": 3.85,
                "battery_pct": 94,
            },
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["data"]["current_value"], 3.85)

        # Ingestion error handling
        bad_resp = self.client.post("/api/iot/telemetry", json={})
        self.assertEqual(bad_resp.status_code, 400)

    # 4. Traffic Sensors
    def test_04_traffic_sensors_source(self):
        weather_by_zone = {w["zone"]: w for w in weather_agent.assess_all_zones()}
        traffic = traffic_agent.assess_all_zones(weather_by_zone)
        self.assertGreater(len(traffic), 0)
        t0 = traffic[0]
        self.assertIn("congestion_level", t0)
        self.assertIn("confidence", t0)
        self.assertIn("road_status", t0)
        self.assertIn("blocked", t0)

    # 5. Hospital Databases
    def test_05_hospital_databases_source(self):
        weather_by_zone = {w["zone"]: w for w in weather_agent.assess_all_zones()}
        hospitals = hospital_agent.get_all_hospitals_with_status(weather_by_zone)
        self.assertGreater(len(hospitals), 0)
        h0 = hospitals[0]
        self.assertIn("hospital_name", h0)
        self.assertIn("beds_available", h0)
        self.assertIn("accessibility_status", h0)

    # 6. Social Media SOS Distress Intelligence
    def test_06_social_media_distress_source(self):
        # NLP Classification
        crit_res = social_media_agent.classify_distress("Family trapped in submerged house, boat needed immediately!")
        self.assertEqual(crit_res["urgency_level"], "Critical")
        self.assertIn("trapped", crit_res["matched_keywords"])

        mod_res = social_media_agent.classify_distress("Heavy traffic jam and waterlogging near junction.")
        self.assertEqual(mod_res["urgency_level"], "Moderate")

        # Ingestion via API
        resp = self.client.post(
            "/api/social/distress",
            json={
                "platform": "Twitter",
                "username": "citizen_bangalore",
                "message": "Water rising rapidly near Bellandur lake bridge, elderly folks stranded!",
                "zone": "Bellandur",
            },
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["post"]["urgency_level"], "Critical")

        # Feed retrieval
        feed_resp = self.client.get("/api/social/distress")
        self.assertEqual(feed_resp.status_code, 200)
        feed_data = feed_resp.get_json()
        self.assertGreater(len(feed_data["distress_feed"]), 0)

        # Platform filter tests: strictly Twitter, Instagram, Facebook
        for plat in ["Twitter", "Instagram", "Facebook"]:
            p_resp = self.client.get(f"/api/social/distress?platform={plat}")
            self.assertEqual(p_resp.status_code, 200)
            items = p_resp.get_json()["distress_feed"]
            for item in items:
                self.assertEqual(item["platform"], plat)

        # Verified filter test
        v_resp = self.client.get("/api/social/distress?verified=1")
        self.assertEqual(v_resp.status_code, 200)
        for item in v_resp.get_json()["distress_feed"]:
            self.assertEqual(item["verified"], 1)

        # Social sync endpoint test
        sync_resp = self.client.post("/api/social/sync")
        self.assertEqual(sync_resp.status_code, 200)
        self.assertTrue(sync_resp.get_json()["ok"])

    # 7. Citizen Emergency Reports
    def test_07_emergency_reports_source(self):
        # Citizen report submission via web form
        form_data = {
            "csrf_token": "test-csrf-token",
            "reporter_name": "Ravi Kumar",
            "reporter_phone": "+919876543210",
            "disaster_type": "Flash Flooding",
            "location": "Koramangala 4th Block",
            "lat": "12.9352",
            "lon": "77.6245",
            "description": "Basement parking submerged, 4 cars floating.",
            "severity": "Critical",
        }
        submit_resp = self.client.post("/citizen/report", data=form_data, follow_redirects=True)
        self.assertEqual(submit_resp.status_code, 200)

        # Dispatcher queue check
        queue_resp = self.client.get("/api/emergency/reports")
        self.assertEqual(queue_resp.status_code, 200)
        reports = queue_resp.get_json()["reports"]
        self.assertGreater(len(reports), 0)
        newest = reports[0]
        self.assertEqual(newest["reporter_name"], "Ravi Kumar")

        # Status update by dispatcher
        status_resp = self.client.post(
            f"/api/emergency/reports/{newest['id']}/status",
            headers=self.headers,
            json={"status": "Dispatched"},
        )
        self.assertEqual(status_resp.status_code, 200)
        self.assertEqual(status_resp.get_json()["status"], "Dispatched")

    # 8. Historical Data & Event Log
    def test_08_historical_data_source(self):
        resp = self.client.get("/api/incidents")
        self.assertEqual(resp.status_code, 200)
        incidents = resp.get_json()
        self.assertIsInstance(incidents, list)

    # Coordinator Comprehensive Data Payload
    def test_09_coordinator_full_report_delivers_all_sources(self):
        report = coordinator_agent.full_report()
        self.assertIn("weather", report)
        self.assertIn("traffic", report)
        self.assertIn("all_hospitals", report)
        self.assertIn("iot_sensors", report)
        self.assertIn("satellite", report)
        self.assertIn("social_distress", report)
        self.assertIn("emergency_reports", report)

    # Consolidated Map Data API Delivers All Map Layers
    def test_10_api_map_data_delivers_all_sources(self):
        resp = self.client.get("/api/map/data")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertIn("zones", data)
        self.assertIn("hospitals", data)
        self.assertIn("rescue_teams", data)
        self.assertIn("iot_sensors", data)
        self.assertIn("satellite", data)
        self.assertIn("social_distress", data)


if __name__ == "__main__":
    unittest.main()
