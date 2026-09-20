import unittest
from database.db import init_db, get_recent_notifications
from api.notification_service import (
    notification_service,
    send_alert_email,
    send_sms_alert,
    send_whatsapp_alert,
    subscribe,
    unsubscribe,
)


class TestNotificationService(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        init_db(seed=True)

    def test_send_alert_email_simulation(self):
        res = send_alert_email(
            to_email="resident@disaster-response.local",
            subject="Test Emergency Subject",
            body="This is a test disaster notice.",
            zone="HSR Layout",
        )
        self.assertTrue(res["ok"])
        self.assertEqual(res["channel"], "email")

        # Verify recorded in notifications table
        history = get_recent_notifications(10)
        found = any(n["recipient"] == "resident@disaster-response.local" and n["title"] == "Test Emergency Subject" for n in history)
        self.assertTrue(found)

    def test_send_sms_alert_simulation(self):
        res = send_sms_alert(
            to_phone="+91-98800-TEST-01",
            message="Test SMS alert from Nirvaha",
            zone="Koramangala",
        )
        self.assertTrue(res["ok"])
        self.assertEqual(res["channel"], "sms")

        history = get_recent_notifications(10)
        found = any(n["recipient"] == "+91-98800-TEST-01" for n in history)
        self.assertTrue(found)

    def test_send_whatsapp_alert_simulation(self):
        test_phone = "+919880099887"
        res = send_whatsapp_alert(
            to_phone=test_phone,
            message="Test WhatsApp alert from Nirvaha Command",
            zone="Indiranagar",
        )
        self.assertTrue(res["ok"])
        self.assertEqual(res["channel"], "whatsapp")

        history = get_recent_notifications(50)
        found = any(n["recipient"] == test_phone and n["channel"] == "whatsapp" for n in history)
        self.assertTrue(found)

    def test_dispatch_broadcast(self):
        res = notification_service.dispatch_broadcast(
            title="Citywide Flood Warning",
            message="Please stay indoors until storm passes.",
            zone="Bellandur",
            channels=["push", "email", "sms", "whatsapp"],
            sender="Duty Officer",
        )
        self.assertTrue(res["ok"])
        self.assertIn("push", res["channels"])
        self.assertIn("whatsapp", res["channels"])
        self.assertGreaterEqual(res["dispatched"]["push"], 1)

    def test_sse_subscribe_and_publish(self):
        q = subscribe()
        try:
            notification_service.dispatch_broadcast(
                title="SSE Test Alert",
                message="Testing SSE delivery",
                channels=["push", "sms", "whatsapp"],
            )
            event = q.get(timeout=2)
            self.assertEqual(event["type"], "emergency_broadcast")
            self.assertEqual(event["data"]["title"], "SSE Test Alert")
            self.assertIn("sms_text", event["data"])
            self.assertIn("whatsapp_text", event["data"])
        finally:
            unsubscribe(q)


if __name__ == "__main__":
    unittest.main()
