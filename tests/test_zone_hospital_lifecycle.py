import unittest
from database.db import init_db, query, add_zone, delete_zone, bulk_insert_zones, bulk_insert_hospitals


class TestZoneHospitalLifecycle(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        init_db(seed=True)

    def test_auto_add_real_hospitals_on_zone_creation(self):
        zone_name = "Whitefield Test Zone"
        lat = 12.9756
        lon = 77.7289
        density = 0.85

        # Cleanup in case previous run left it
        z_exist = query("SELECT id FROM zones WHERE name = ?", (zone_name,), fetchone=True)
        if z_exist:
            delete_zone(z_exist["id"])

        # 1. Add zone
        zone_id = add_zone(zone_name, lat, lon, density)
        self.assertIsNotNone(zone_id)

        # 2. Verify hospitals were automatically discovered and inserted
        hosps = query("SELECT * FROM hospitals WHERE location = ?", (zone_name,))
        self.assertGreaterEqual(len(hosps), 1)

        # 3. Verify hospitals have authentic names and valid phone contacts (no fake text)
        for h in hosps:
            self.assertFalse("Medical Centre" in h["hospital_name"] and "Zone Service Desk" in h["contact"])
            self.assertNotEqual(h["contact"], "Zone Service Desk")
            self.assertTrue(len(h["contact"]) >= 3)
            self.assertGreater(h["beds_total"], 0)
            self.assertGreater(h["beds_available"], 0)
            self.assertGreater(h["doctors_available"], 0)

        # 4. Verify no duplicates even if add_zone is called again
        add_zone(zone_name, lat, lon, density)
        hosps_after = query("SELECT * FROM hospitals WHERE location = ?", (zone_name,))
        self.assertEqual(len(hosps), len(hosps_after))

        # Register a test citizen in that zone
        from database.db import create_citizen
        test_uid = create_citizen("Test Zone Citizen", "testzonecitizen@example.com", "Password123!", zone_name, "+919876543210")
        self.assertIsNotNone(test_uid)

        # 5. Delete zone and verify all hospitals and users for that location are completely removed
        delete_zone(zone_id)
        hosps_remaining = query("SELECT * FROM hospitals WHERE location = ?", (zone_name,))
        self.assertEqual(len(hosps_remaining), 0)

        # Users in that zone must also be deleted
        user_remaining = query("SELECT id FROM users WHERE id = ?", (test_uid,), fetchone=True)
        self.assertIsNone(user_remaining)

        # Zone itself must be gone
        z_remaining = query("SELECT * FROM zones WHERE id = ?", (zone_id,), fetchone=True)
        self.assertIsNone(z_remaining)

    def test_arbitrary_coords_auto_hospital_population(self):
        # Coordinates in Bangalore area (Malleshwaram area)
        zone_name = "Malleshwaram North"
        lat = 13.0035
        lon = 77.5712

        z_exist = query("SELECT id FROM zones WHERE name = ?", (zone_name,), fetchone=True)
        if z_exist:
            delete_zone(z_exist["id"])

        zone_id = add_zone(zone_name, lat, lon, 0.7)
        hosps = query("SELECT * FROM hospitals WHERE location = ?", (zone_name,))
        self.assertGreater(len(hosps), 0)

        for h in hosps:
            self.assertNotEqual(h["contact"], "Zone Service Desk")
            self.assertTrue(len(h["hospital_name"]) > 3)

        # Clean up
        delete_zone(zone_id)
        self.assertEqual(len(query("SELECT * FROM hospitals WHERE location = ?", (zone_name,))), 0)

    def test_bulk_insert_hospitals_deduplication(self):
        zone_name = "HSR Layout"
        rows = [
            {
                "hospital_name": "Columbia Asia Hospital, Sarjapur Road",
                "location": zone_name,
                "lat": 12.9101,
                "lon": 77.6520,
                "beds_total": 180,
                "beds_available": 50,
                "doctors_available": 30,
                "ambulances_available": 5,
                "icu_available": 10,
                "contact": "080-6165-6262",
            }
        ]

        count_before = query("SELECT COUNT(*) AS c FROM hospitals WHERE location = ?", (zone_name,), fetchone=True)["c"]
        res = bulk_insert_hospitals(rows)
        self.assertEqual(res["imported"], 1)
        count_after = query("SELECT COUNT(*) AS c FROM hospitals WHERE location = ?", (zone_name,), fetchone=True)["c"]
        # Must not have created duplicate rows
        self.assertEqual(count_before, count_after)


if __name__ == "__main__":
    unittest.main()
