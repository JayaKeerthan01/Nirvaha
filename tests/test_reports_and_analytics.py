import unittest
from app import app
from database.db import init_db, query


class TestReportsAndAnalytics(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        init_db(seed=True)
        cls.client = app.test_client()

    def setUp(self):
        with self.client.session_transaction() as sess:
            sess["user_id"] = 1
            sess["role"] = "admin"
            sess["user_name"] = "Admin Duty Officer"
            sess["csrf_token"] = "valid-test-csrf-token"
        self.headers = {"X-CSRFToken": "valid-test-csrf-token"}

    def test_reports_views(self):
        # Daily report view
        resp_daily = self.client.get("/reports?period=daily")
        self.assertEqual(resp_daily.status_code, 200)
        self.assertIn("Daily Operations Report", resp_daily.get_data(as_text=True))

        # Monthly report view
        resp_monthly = self.client.get("/reports?period=monthly")
        self.assertEqual(resp_monthly.status_code, 200)
        self.assertIn("Monthly Command Summary", resp_monthly.get_data(as_text=True))

    def test_reports_csv_exports(self):
        # Export incidents CSV
        resp_inc = self.client.get("/reports/export/incidents.csv")
        self.assertEqual(resp_inc.status_code, 200)
        self.assertIn("text/csv", resp_inc.content_type)
        csv_text = resp_inc.get_data(as_text=True)
        self.assertIn("Disaster Type", csv_text)

        # Export deployments CSV
        resp_dep = self.client.get("/reports/export/deployments.csv")
        self.assertEqual(resp_dep.status_code, 200)
        self.assertIn("text/csv", resp_dep.content_type)
        csv_dep_text = resp_dep.get_data(as_text=True)
        self.assertIn("Team Name", csv_dep_text)

    def test_reports_summary_api(self):
        resp = self.client.get("/api/reports/summary")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertIn("incidents_today", data)
        self.assertIn("total_deployments", data)

    def test_citizen_shelter_routing(self):
        # Fetch shelter route for Koramangala
        resp = self.client.get("/api/citizen/shelter?zone=Koramangala")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertIn("shelter", data)
        self.assertIn("distance_km", data)
        self.assertIn("duration_min", data)
        self.assertIn("path", data)
        self.assertGreater(len(data["path"]), 0)


if __name__ == "__main__":
    unittest.main()
