import unittest
from datetime import datetime, timedelta
from app import app
from database.db import (
    init_db, query, get_user_by_email,
    create_password_reset_code, verify_password_reset_code, consume_password_reset,
    log_audit, get_audit_logs, bulk_insert_zones, bulk_insert_hospitals, bulk_insert_teams,
)
from werkzeug.security import generate_password_hash, check_password_hash
import hashlib


def hash_code(code):
    return hashlib.sha256(code.encode()).hexdigest()


class TestAuditAndSecurity(unittest.TestCase):
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

    def test_password_reset_lifecycle(self):
        user = get_user_by_email("admin@disaster-response.local")
        self.assertIsNotNone(user)

        code = "789123"
        code_h = hash_code(code)
        expires_at = (datetime.utcnow() + timedelta(minutes=15)).strftime("%Y-%m-%d %H:%M:%S")

        # 1. Create code
        create_password_reset_code(user["id"], code_h, expires_at)

        # 2. Verify code
        self.assertTrue(verify_password_reset_code(user["id"], code_h))

        # Wrong code fails
        self.assertFalse(verify_password_reset_code(user["id"], hash_code("000000")))

        # 3. Consume reset code
        new_pwd = "NewAdminPassword123!"
        consume_password_reset(user["id"], generate_password_hash(new_pwd))

        # Check user password updated
        updated_user = get_user_by_email("admin@disaster-response.local")
        self.assertTrue(check_password_hash(updated_user["password_hash"], new_pwd))

        # Used code cannot be reused
        self.assertFalse(verify_password_reset_code(user["id"], code_h))

        # Restore original password for subsequent tests
        consume_password_reset(user["id"], generate_password_hash("admin123"))

    def test_audit_logging_and_filtering(self):
        # Log custom actions
        log_audit(
            user_id=1,
            user_name="Admin Duty Officer",
            role="admin",
            action="UNIT_TEST_ACTION",
            target_type="system",
            target_id="test-1",
            details="Test audit execution details",
            ip_address="127.0.0.1",
        )

        logs = get_audit_logs(limit=50, action="UNIT_TEST_ACTION")
        self.assertGreater(len(logs), 0)
        self.assertEqual(logs[0]["action"], "UNIT_TEST_ACTION")
        self.assertEqual(logs[0]["target_id"], "test-1")

        # Check endpoint
        resp = self.client.get("/api/admin/audit?action=UNIT_TEST_ACTION")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertGreater(len(data), 0)

    def test_bulk_data_ingestion(self):
        # 1. Bulk insert zones
        zone_rows = [
            {"name": "Electronic City Phase 1", "lat": 12.8452, "lon": 77.6602, "density": 0.72},
            {"name": "Electronic City Phase 2", "lat": 12.8400, "lon": 77.6750, "density": 0.68},
        ]
        res_z = bulk_insert_zones(zone_rows)
        self.assertEqual(res_z["imported"], 2)
        self.assertEqual(len(res_z["errors"]), 0)

        # Check zone exists
        z_check = query("SELECT * FROM zones WHERE name = 'Electronic City Phase 1'", fetchone=True)
        self.assertIsNotNone(z_check)

        # 2. Bulk insert hospitals
        hosp_rows = [
            {
                "hospital_name": "Apex Trauma Center",
                "location": "Electronic City Phase 1",
                "lat": 12.8450,
                "lon": 77.6610,
                "beds_total": 80,
                "beds_available": 25,
                "doctors_available": 12,
                "ambulances_available": 3,
                "icu_available": 6,
                "contact": "080-28520000",
            }
        ]
        res_h = bulk_insert_hospitals(hosp_rows)
        self.assertEqual(res_h["imported"], 1)

        # 3. Bulk insert teams
        team_rows = [
            {
                "team_name": "Echo Force Rescue",
                "zone": "Electronic City Phase 1",
                "lat": 12.8460,
                "lon": 77.6620,
                "vehicles": 4,
                "ambulances": 2,
                "fire_units": 1,
                "personnel": 15,
                "status": "available",
            }
        ]
        res_t = bulk_insert_teams(team_rows)
        self.assertEqual(res_t["imported"], 1)


if __name__ == "__main__":
    unittest.main()
