import unittest
from app import app
from database.db import (
    init_db,
    get_shelters_with_supplies,
    get_shelter_supplies,
    update_shelter_supply,
    update_shelter_occupancy,
    get_relief_volunteers,
    get_press_releases,
    add_press_release,
    get_stakeholders_summary,
)


class TestExternalStakeholders(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        init_db(seed=True)
        app.config["TESTING"] = True
        cls.client = app.test_client()

    def test_shelters_with_supplies(self):
        shelters = get_shelters_with_supplies()
        self.assertGreaterEqual(len(shelters), 1)
        s0 = shelters[0]
        self.assertIn("supplies", s0)
        self.assertIn("occupancy_pct", s0)
        self.assertGreaterEqual(len(s0["supplies"]), 1)

    def test_update_supply_quantity(self):
        shelters = get_shelters_with_supplies()
        sup = shelters[0]["supplies"][0]
        updated = update_shelter_supply(sup["id"], 500)
        self.assertEqual(updated["quantity"], 500)
        self.assertEqual(updated["status"], "Adequate")

        # Test critical threshold
        updated_crit = update_shelter_supply(sup["id"], 10)
        self.assertEqual(updated_crit["status"], "Critical")

    def test_update_shelter_occupancy(self):
        shelters = get_shelters_with_supplies()
        s0 = shelters[0]
        updated = update_shelter_occupancy(s0["id"], 250)
        self.assertEqual(updated["current_occupancy"], 250)

    def test_relief_volunteers(self):
        volunteers = get_relief_volunteers()
        self.assertGreaterEqual(len(volunteers), 1)
        v0 = volunteers[0]
        self.assertIn("ngo_name", v0)
        self.assertIn("active_volunteers", v0)

    def test_press_releases_and_feed(self):
        import time
        bulletin_no = f"TEST-SITREP-{int(time.time() * 1000)}"
        rel = add_press_release(
            bulletin_no=bulletin_no,
            headline="Test Cyclone Warning Issued",
            zone="Bellandur",
            disaster_type="Cyclone",
            official_statement="All residents advised to stay sheltered.",
            verified_casualties=0,
            evacuees_count=100,
            sheltered_count=80,
        )
        self.assertIsNotNone(rel)
        self.assertEqual(rel["bulletin_no"], bulletin_no)

        releases = get_press_releases(published_only=True)
        found = any(r["bulletin_no"] == bulletin_no for r in releases)
        self.assertTrue(found)

    def test_stakeholders_summary(self):
        summary = get_stakeholders_summary()
        self.assertIn("authorities", summary)
        self.assertIn("emergency_services", summary)
        self.assertIn("hospitals", summary)
        self.assertIn("citizens", summary)
        self.assertIn("ngos", summary)
        self.assertIn("media", summary)

    def test_public_media_endpoints(self):
        # Public press desk does not require authentication
        res = self.client.get("/press")
        self.assertEqual(res.status_code, 200)

        # Public JSON feed does not require authentication
        feed_res = self.client.get("/api/press/feed")
        self.assertEqual(feed_res.status_code, 200)
        feed_data = feed_res.get_json()
        self.assertTrue(feed_data["ok"])
        self.assertIn("releases", feed_data)
        self.assertIn("verified_statistics", feed_data)

    def test_relief_portal_access(self):
        # Unauthenticated access to relief portal should redirect to login
        res_unauth = self.client.get("/relief")
        self.assertEqual(res_unauth.status_code, 302)

        # Authenticated staff access
        with self.client.session_transaction() as sess:
            sess["user_id"] = 1
            sess["user_name"] = "Admin Officer"
            sess["role"] = "admin"

        res_auth = self.client.get("/relief")
        self.assertEqual(res_auth.status_code, 200)

        overview_res = self.client.get("/api/relief/overview")
        self.assertEqual(overview_res.status_code, 200)
        data = overview_res.get_json()
        self.assertTrue(data["ok"])
        self.assertIn("shelters", data)
        self.assertIn("kpi", data)

        # Stakeholders matrix page
        stk_res = self.client.get("/stakeholders")
        self.assertEqual(stk_res.status_code, 200)


if __name__ == "__main__":
    unittest.main()
