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
    enroll_whatsapp_subscriber,
    get_whatsapp_subscribers,
    get_whatsapp_subscribers_count,
)
from api.notification_service import notification_service


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

    def test_whatsapp_broadcast_channel_enrollment(self):
        import time
        t = int(time.time())
        test_phone = f"+9198765{t % 100000:05d}"
        test_email = f"wa_user_{t}@test.com"
        test_name = f"WA Citizen {t}"

        # 1. Direct enrollment test
        sub = enroll_whatsapp_subscriber(None, test_name, test_phone, zone="Indiranagar")
        self.assertIsNotNone(sub)
        self.assertEqual(sub["phone"], test_phone)
        self.assertEqual(sub["status"], "Subscribed")

        # 2. Get subscribers query
        subs_zone = get_whatsapp_subscribers(zone="Indiranagar")
        self.assertTrue(any(s["phone"] == test_phone for s in subs_zone))
        self.assertGreaterEqual(get_whatsapp_subscribers_count(), 1)

        # 3. Welcome notification test
        welcome_res = notification_service.dispatch_whatsapp_channel_welcome(test_name, test_phone, zone="Indiranagar")
        self.assertTrue(welcome_res.get("ok"))
        self.assertEqual(welcome_res.get("channel"), "whatsapp")

        # 4. Signup flow auto-enrollment test
        signup_phone = f"+9191234{t % 100000:05d}"
        signup_email = f"signup_wa_{t}@example.com"
        with self.client.session_transaction() as sess:
            sess["csrf_token"] = "test-csrf-token"
        res_signup = self.client.post("/signup", data={
            "csrf_token": "test-csrf-token",
            "name": f"Citizen {t}",
            "email": signup_email,
            "password": "Password123!",
            "phone": signup_phone,
            "zone": "Koramangala",
        }, follow_redirects=False)
        self.assertEqual(res_signup.status_code, 302)

        # Verify enrolled into whatsapp_subscribers
        enrolled = get_whatsapp_subscribers(zone="Koramangala")
        self.assertTrue(any(s["phone"] == signup_phone for s in enrolled))


if __name__ == "__main__":
    unittest.main()
