import unittest
from app import app
from database.db import init_db


class TestSystemIntegration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        init_db(seed=True)
        cls.client = app.test_client()

    def setUp(self):
        with self.client.session_transaction() as sess:
            sess["user_id"] = 1
            sess["role"] = "admin"
            sess["user_name"] = "Duty Officer"
            sess["csrf_token"] = "valid-test-csrf-token"
        self.headers = {"X-CSRFToken": "valid-test-csrf-token"}

    def test_dashboard_widgets_rendered(self):
        resp = self.client.get("/dashboard")
        self.assertEqual(resp.status_code, 200)
        html = resp.get_data(as_text=True)
        self.assertIn("emergency-ticker", html)
        self.assertIn("Hospital Capacity", html)
        self.assertIn("Rescue Force Readiness", html)
        self.assertIn("map-command-toolbar", html)
        self.assertIn("auto-assignment-rows", html)

    def test_alerts_page_notification_logs(self):
        resp = self.client.get("/alerts")
        self.assertEqual(resp.status_code, 200)
        html = resp.get_data(as_text=True)
        self.assertIn("notification-rows", html)
        self.assertIn("page-broadcast-form", html)

    def test_emergency_broadcast_and_notification_log(self):
        bc = self.client.post(
            "/api/notifications/broadcast",
            headers=self.headers,
            json={
                "title": "Flash Flood Warning",
                "message": "Immediate evacuation advisory for low lying areas.",
                "zone": "HSR Layout",
                "channels": ["push", "email", "sms"],
            },
        )
        self.assertEqual(bc.status_code, 200)
        data = bc.get_json()
        self.assertTrue(data["ok"])

        # Check retrieval in notifications history
        n_resp = self.client.get("/api/notifications")
        self.assertEqual(n_resp.status_code, 200)
        history = n_resp.get_json()["history"]
        self.assertGreater(len(history), 0)

    def test_map_data_endpoint(self):
        resp = self.client.get("/api/map/data")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertIn("zones", data)
        self.assertIn("hospitals", data)
        self.assertIn("rescue_teams", data)
        self.assertIn("assignments", data)

    def test_auto_assignment_and_toggle(self):
        # Test toggle
        t_resp = self.client.post("/api/auto-assign/toggle", headers=self.headers, json={"enabled": True})
        self.assertEqual(t_resp.status_code, 200)
        self.assertTrue(t_resp.get_json()["auto_dispatch_active"])

        # Test execute single zone
        exec_resp = self.client.post("/api/auto-assign/execute", headers=self.headers, json={"zone": "HSR Layout"})
        self.assertIn(exec_resp.status_code, (200, 409))

        # Test execute batch all
        batch_resp = self.client.post("/api/auto-assign/execute", headers=self.headers, json={"all": True})
        self.assertEqual(batch_resp.status_code, 200)


if __name__ == "__main__":
    unittest.main()
