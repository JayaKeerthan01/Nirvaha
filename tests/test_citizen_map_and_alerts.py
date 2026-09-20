import unittest
from werkzeug.security import generate_password_hash
from app import app, is_valid_email, is_valid_phone, normalize_phone
from utils.email_validator import validate_email_for_signup
from database.db import (
    init_db, query, create_citizen, get_user_by_email, get_user_by_id,
    get_users_by_zone, get_recent_notifications, mark_email_verified,
)
from api.notification_service import notification_service


class TestCitizenMapAndAlerts(unittest.TestCase):
    def setUp(self):
        self.app = app
        self.app.config["TESTING"] = True
        self.client = self.app.test_client()
        init_db(seed=True)

    def test_strict_email_validation(self):
        """Ensure strict RFC validation blocks malformed, typo-ridden emails."""
        valid_emails = [
            "citizen@example.com",
            "first.last@disaster.gov.in",
            "rescue_team+alert@sub.domain.org",
            "resident123@emergency.net",
        ]
        for email in valid_emails:
            self.assertTrue(is_valid_email(email), f"Expected valid: {email}")

        invalid_emails = [
            "",
            "plainaddress",
            "@missinguser.com",
            "user@domain",
            "user@.com",
            "bad..dots@domain.com",
            "user@domain..com",
            "spaces in@email.com",
            "user@domain.c",  # Single letter TLD
            "user@-domain.com",
        ]
        for email in invalid_emails:
            self.assertFalse(is_valid_email(email), f"Expected invalid: {email}")

    def test_mobile_number_validation_and_normalization(self):
        """Ensure 10-15 digit phone validation and formatting."""
        valid_phones = [
            ("+91 98765 43210", "+919876543210"),
            ("9876543210", "9876543210"),
            ("+91-99887-66554", "+919988766554"),
            ("+1-800-555-0199", "+18005550199"),
        ]
        for raw, expected_norm in valid_phones:
            self.assertTrue(is_valid_phone(raw), f"Expected valid phone: {raw}")
            self.assertEqual(normalize_phone(raw), expected_norm)

        invalid_phones = [
            "",
            "123",
            "phone123",
            "987654321",  # 9 digits (too short)
            "12345678901234567",  # 17 digits (too long)
            "++919876543210",
            "letters-in-phone",
        ]
        for phone in invalid_phones:
            self.assertFalse(is_valid_phone(phone), f"Expected invalid phone: {phone}")

    def test_citizen_signup_stores_phone_and_strict_email(self):
        """Test signup flow requiring valid mobile phone and strict email."""
        # 1. Reject invalid email
        with self.client.session_transaction() as sess:
            sess["csrf_token"] = "test-csrf-token"
        resp = self.client.post("/signup", data={
            "csrf_token": "test-csrf-token",
            "name": "Test Citizen",
            "email": "invalid-email-address",
            "phone": "+91 98765 43210",
            "password": "Password123!",
            "zone": "HSR Layout",
        }, follow_redirects=True)
        self.assertIn(b"Please enter a valid, active email address", resp.data)

        # 2. Reject invalid phone
        with self.client.session_transaction() as sess:
            sess["csrf_token"] = "test-csrf-token"
        resp = self.client.post("/signup", data={
            "csrf_token": "test-csrf-token",
            "name": "Test Citizen",
            "email": "valid.resident@example.com",
            "phone": "123",  # invalid
            "password": "Password123!",
            "zone": "HSR Layout",
        }, follow_redirects=True)
        self.assertIn(b"A valid 10 to 15-digit mobile number is required", resp.data)

        # 3. Successful signup with phone and email
        test_email = "citizen.safety@example.com"
        query("DELETE FROM users WHERE email = ?", (test_email,))
        with self.client.session_transaction() as sess:
            sess["csrf_token"] = "test-csrf-token"
        resp = self.client.post("/signup", data={
            "csrf_token": "test-csrf-token",
            "name": "Citizen Safety",
            "email": test_email,
            "phone": "+91 98123 45678",
            "password": "SecurePassword123!",
            "zone": "Indiranagar",
        }, follow_redirects=True)
        self.assertEqual(resp.status_code, 200)

        # Check in DB
        u = get_user_by_email(test_email)
        self.assertIsNotNone(u)
        self.assertEqual(u["name"], "Citizen Safety")
        self.assertEqual(u["phone"], "+919812345678")
        self.assertEqual(u["zone"], "Indiranagar")

    def test_emergency_sms_dispatched_to_citizen_phone(self):
        """Verify SMS notifications reach registered citizen phone numbers."""
        test_email = "sms_resident@disaster.test"
        query("DELETE FROM users WHERE email = ?", (test_email,))
        target_phone = "+919877700001"
        create_citizen(
            name="SMS Resident",
            email=test_email,
            password_hash=generate_password_hash("pass123"),
            zone="Koramangala",
            phone=target_phone
        )

        # Dispatch high-risk alert for Koramangala
        notification_service.dispatch_high_risk_alert("Koramangala", {
            "prediction": "Flash Flood",
            "risk_score": 0.88,
            "weather": {"rainfall_mm": 110, "wind_speed_kmh": 45}
        })

        # Verify notification was logged with recipient = target_phone
        notifs = query(
            "SELECT * FROM notifications WHERE channel = 'sms' AND recipient = ? ORDER BY id DESC LIMIT 1",
            (target_phone,),
            fetchone=True
        )
        self.assertIsNotNone(notifs)
        self.assertIn("HIGH RISK in Koramangala", notifs["message"])

    def test_citizen_evacuation_page_and_map_endpoint(self):
        """Verify citizen evacuation view loads Leaflet assets and map container."""
        # Login as resident
        with self.client.session_transaction() as sess:
            sess["user_id"] = 999
            sess["user_name"] = "Resident"
            sess["role"] = "citizen"
            sess["zone"] = "HSR Layout"

        resp = self.client.get("/citizen/evacuation")
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b'id="evacuation-map"', resp.data)
        self.assertIn(b"leaflet.css", resp.data)
        self.assertIn(b"leaflet.js", resp.data)
        self.assertIn(b"citizen_evacuation.js", resp.data)

        # Verify shelter API provides map coordinates & polyline path
        shelter_resp = self.client.get("/api/citizen/shelter?zone=HSR%20Layout")
        self.assertEqual(shelter_resp.status_code, 200)
        data = shelter_resp.get_json()
        self.assertIn("shelter", data)
        self.assertIn("from_coords", data)
        self.assertIn("shelter_coords", data)
        self.assertIn("path", data)
        self.assertTrue(len(data["path"]) > 1)

    def test_anti_fake_email_detection(self):
        """Verify anti-fake email validator catches disposable domains, bogus domains,
        and placeholder usernames."""
        # Disposable burner emails must be rejected
        disposable_emails = [
            "resident@mailinator.com",
            "alert@tempmail.com",
            "citizen@10minutemail.com",
            "emergency@guerrillamail.com",
            "victim@yopmail.com",
            "user@sharklasers.com",
            "test@dispostable.com",
        ]
        for email in disposable_emails:
            ok, reason = validate_email_for_signup(email, is_testing=False)
            self.assertFalse(ok, f"Expected disposable email to be blocked: {email}")
            self.assertIn("Disposable and temporary email addresses", reason)

        # Bogus / placeholder domains must be rejected
        bogus_domain_emails = [
            "realuser@fake.com",
            "citizen@test.com",
            "resident@dummy.com",
            "alert@asdf.com",
            "person@junk.com",
        ]
        for email in bogus_domain_emails:
            ok, reason = validate_email_for_signup(email, is_testing=False)
            self.assertFalse(ok, f"Expected bogus domain to be blocked: {email}")
            self.assertIn("placeholder or fake domain", reason)

        # Bogus / placeholder usernames must be rejected
        bogus_usernames = [
            "test@gmail.com",
            "fake@gmail.com",
            "dummy@yahoo.com",
            "asdf@outlook.com",
            "qwerty@gmail.com",
            "aaaaaa@gmail.com",
            "123456@gmail.com",
            "noreply@customdomain.org",
        ]
        for email in bogus_usernames:
            ok, reason = validate_email_for_signup(email, is_testing=False)
            self.assertFalse(ok, f"Expected bogus username to be blocked: {email}")
            self.assertIn("genuine personal or business email", reason)

        # Non-existent domains must be rejected via DNS verification in non-testing mode
        non_existent_email = "citizen@totallynonexistentdomain12344321xyz.com"
        ok, reason = validate_email_for_signup(non_existent_email, is_testing=False)
        self.assertFalse(ok, f"Expected non-existent domain to fail DNS check: {non_existent_email}")
        self.assertIn("does not appear to exist", reason)

    def test_signup_rejects_disposable_and_fake_emails_on_post(self):
        """Verify /signup route rejects disposable emails on POST submission."""
        with self.client.session_transaction() as sess:
            sess["csrf_token"] = "test-csrf-token"

        # Attempt signup with disposable mailinator domain
        resp = self.client.post("/signup", data={
            "csrf_token": "test-csrf-token",
            "name": "Throwaway Citizen",
            "email": "burnercitizen@mailinator.com",
            "phone": "+91 98765 43210",
            "password": "Password123!",
            "zone": "Koramangala",
        }, follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"Disposable and temporary email addresses", resp.data)

        # Attempt signup with fake domain
        resp2 = self.client.post("/signup", data={
            "csrf_token": "test-csrf-token",
            "name": "Fake Citizen",
            "email": "somebody@fake.com",
            "phone": "+91 98765 43210",
            "password": "Password123!",
            "zone": "Koramangala",
        }, follow_redirects=True)
        self.assertEqual(resp2.status_code, 200)
        self.assertIn(b"placeholder or fake domain", resp2.data)

    def test_login_requires_valid_email_and_blocks_unverified(self):
        """Verify that login strictly validates email syntax, blocks unverified accounts,
        and permits verified accounts."""
        with self.client.session_transaction() as sess:
            sess["csrf_token"] = "test-csrf-token"

        # 1. Reject invalid email format upfront
        resp_invalid_format = self.client.post("/login", data={
            "csrf_token": "test-csrf-token",
            "email": "not-a-valid-email",
            "password": "somepassword123",
        }, follow_redirects=True)
        self.assertIn(b"Please enter a valid email address.", resp_invalid_format.data)

        # 2. Block unverified user account from signing in
        unverified_email = "unverified_citizen@disaster.test"
        query("DELETE FROM users WHERE email = ?", (unverified_email,))
        u_id = create_citizen(
            name="Unverified Citizen",
            email=unverified_email,
            password_hash=generate_password_hash("CorrectPassword123!"),
            zone="HSR Layout",
            phone="+919876500099",
        )
        # Verify account starts unverified (email_verified = 0)
        user = get_user_by_id(u_id)
        self.assertEqual(user["email_verified"], 0)

        # Attempt to login with unverified account
        with self.client.session_transaction() as sess:
            sess["csrf_token"] = "test-csrf-token"
        resp_unverified = self.client.post("/login", data={
            "csrf_token": "test-csrf-token",
            "email": unverified_email,
            "password": "CorrectPassword123!",
        }, follow_redirects=True)
        self.assertIn(b"Your email address is not verified yet", resp_unverified.data)

        # Ensure user was not logged in
        with self.client.session_transaction() as sess:
            self.assertNotIn("user_id", sess)

        # 3. Mark account as verified and verify login succeeds
        mark_email_verified(u_id)
        with self.client.session_transaction() as sess:
            sess["csrf_token"] = "test-csrf-token"
        resp_verified = self.client.post("/login", data={
            "csrf_token": "test-csrf-token",
            "email": unverified_email,
            "password": "CorrectPassword123!",
        }, follow_redirects=True)
        self.assertEqual(resp_verified.status_code, 200)
        self.assertNotIn(b"Your email address is not verified", resp_verified.data)
        self.assertNotIn(b"Invalid email or password", resp_verified.data)


if __name__ == "__main__":
    unittest.main()
