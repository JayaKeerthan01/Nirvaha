"""
Emergency Notification Service

Handles multi-channel alert distribution across:
  1. Real-time in-app / Push stream (SSE and browser Web Notifications)
  2. Automated Emergency Email alerts (via SMTP or simulation mode)
  3. SMS Alerts (via Twilio when configured, or simulation mode with full delivery logs)
  4. Emergency Broadcasts initiated by command staff
"""

import json
import logging
import smtplib
import queue
import threading
import time
from datetime import datetime
from email.mime.text import MIMEText

from config import Config
from database.db import (
    log_notification,
    get_users_by_zone,
    get_all_contactable_users,
    get_recent_notifications,
)

logger = logging.getLogger(__name__)

# Thread-safe broadcast event bus for real-time SSE stream
_event_subscribers = []
_subscriber_lock = threading.Lock()
_recent_events_cache = []
_MAX_EVENT_HISTORY = 50


def _publish_event(event_type, payload):
    """Broadcast an event to all active SSE client streams and keep in recent history."""
    event = {
        "id": int(time.time() * 1000),
        "type": event_type,
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "data": payload,
    }
    with _subscriber_lock:
        _recent_events_cache.append(event)
        if len(_recent_events_cache) > _MAX_EVENT_HISTORY:
            _recent_events_cache.pop(0)

        dead_subs = []
        for q in _event_subscribers:
            try:
                q.put_nowait(event)
            except Exception:
                dead_subs.append(q)
        for q in dead_subs:
            if q in _event_subscribers:
                _event_subscribers.remove(q)


def subscribe():
    """Register a new SSE stream queue."""
    q = queue.Queue(maxsize=100)
    with _subscriber_lock:
        _event_subscribers.append(q)
    return q


def unsubscribe(q):
    """Deregister an SSE stream queue."""
    with _subscriber_lock:
        if q in _event_subscribers:
            _event_subscribers.remove(q)


def get_recent_live_events(limit=20):
    with _subscriber_lock:
        return list(_recent_events_cache[-limit:])


# ------------------------------------------------------------- Email channel ----

def _smtp_configured():
    return bool(Config.SMTP_HOST and Config.SMTP_USER and Config.SMTP_PASSWORD and Config.SMTP_FROM)


def send_alert_email(to_email, subject, body, zone=None):
    """Sends an emergency email or logs simulated transmission."""
    if not _smtp_configured():
        logger.info("[SIMULATED EMAIL] To: %s | Subject: %s | Zone: %s", to_email, subject, zone)
        log_notification(
            channel="email",
            recipient=to_email,
            title=subject,
            message=body,
            zone=zone,
            status="simulated",
        )
        return {"ok": True, "channel": "email", "recipient": to_email, "mode": "simulated"}

    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"] = Config.SMTP_FROM
    msg["To"] = to_email

    try:
        with smtplib.SMTP(Config.SMTP_HOST, Config.SMTP_PORT, timeout=10) as server:
            server.starttls()
            pwd = str(Config.SMTP_PASSWORD).replace(" ", "").strip()
            server.login(Config.SMTP_USER, pwd)
            server.sendmail(Config.SMTP_FROM, [to_email], msg.as_string())
        log_notification(
            channel="email",
            recipient=to_email,
            title=subject,
            message=body,
            zone=zone,
            status="sent",
        )
        return {"ok": True, "channel": "email", "recipient": to_email, "mode": "sent"}
    except Exception as exc:
        logger.warning("SMTP delivery failed to %s (%s); logging simulated delivery", to_email, exc)
        log_notification(
            channel="email",
            recipient=to_email,
            title=subject,
            message=body,
            zone=zone,
            status="failed",
        )
        return {"ok": False, "error": str(exc), "channel": "email", "recipient": to_email}


# --------------------------------------------------------------- SMS channel ----

def _twilio_configured():
    return bool(
        getattr(Config, "TWILIO_ACCOUNT_SID", None)
        and getattr(Config, "TWILIO_AUTH_TOKEN", None)
        and getattr(Config, "TWILIO_PHONE_NUMBER", None)
    )


def send_sms_alert(to_phone, message, zone=None):
    """Sends an emergency SMS via Twilio if credentials exist, else logs simulated SMS."""
    if not _twilio_configured():
        logger.info("[SIMULATED SMS] To: %s | Zone: %s | Msg: %s", to_phone, zone, message)
        log_notification(
            channel="sms",
            recipient=to_phone,
            title="Emergency SMS Alert",
            message=message,
            zone=zone,
            status="simulated",
        )
        return {"ok": True, "channel": "sms", "recipient": to_phone, "mode": "simulated"}

    try:
        import requests
        url = f"https://api.twilio.com/2010-04-01/Accounts/{Config.TWILIO_ACCOUNT_SID}/Messages.json"
        resp = requests.post(
            url,
            auth=(Config.TWILIO_ACCOUNT_SID, Config.TWILIO_AUTH_TOKEN),
            data={
                "From": Config.TWILIO_PHONE_NUMBER,
                "To": to_phone,
                "Body": message,
            },
            timeout=10,
        )
        resp_data = resp.json() if resp.text else {}
        if resp.status_code not in (200, 201):
            raise RuntimeError(resp_data.get("message", f"Twilio HTTP error {resp.status_code}"))
        sid = resp_data.get("sid", "sent")

        log_notification(
            channel="sms",
            recipient=to_phone,
            title="Emergency SMS Alert",
            message=message,
            zone=zone,
            status="sent",
        )
    except Exception as exc:
        err_str = str(exc)
        logger.warning("Twilio SMS send failed to %s (%s)", to_phone, err_str)
        log_notification(
            channel="sms",
            recipient=to_phone,
            title="Emergency SMS Alert",
            message=f"{message} [Carrier notice: {err_str}]",
            zone=zone,
            status="failed",
        )
        return {"ok": True, "channel": "sms", "recipient": to_phone, "mode": "failed", "warning": err_str}


# -------------------------------------------------- Multi-Channel Dispatcher ----

class NotificationService:
    name = "Notification Service"

    def dispatch_high_risk_alert(self, zone_name, assessment):
        """Triggered automatically when a zone transitions to HIGH risk."""
        hazard = assessment.get("prediction", "Severe Disaster")
        score = int(assessment.get("risk_score", 0.9) * 100)
        weather = assessment.get("weather", {})
        rain = weather.get("rainfall_mm", 0)
        wind = weather.get("wind_speed_kmh", 0)

        title = f"CRITICAL: {hazard} Alert in {zone_name}"
        message = (
            f"URGENT: {zone_name} has escalated to HIGH RISK ({hazard} probability {score}%). "
            f"Rainfall: {rain}mm, Wind: {wind}km/h. "
            f"Seek higher ground or prepare for evacuation. Follow official routes."
        )

        # 1. Publish real-time push event for active browser clients
        _publish_event("high_risk_alert", {
            "zone": zone_name,
            "hazard": hazard,
            "score": score,
            "severity": "High",
            "title": title,
            "message": message,
            "weather": weather,
        })
        log_notification(
            channel="push",
            recipient="broadcast_all",
            title=title,
            message=message,
            zone=zone_name,
            status="sent",
        )

        # 2. Email alerts to registered citizens in that zone
        citizens = get_users_by_zone(zone_name)
        for c in citizens:
            email = c.get("email")
            if email:
                email_body = (
                    f"Dear {c.get('name', 'Resident')},\n\n"
                    f"This is an automated emergency warning from Nirvaha Disaster Command.\n\n"
                    f"Your registered district, {zone_name}, has entered HIGH RISK condition:\n"
                    f"- Hazard: {hazard}\n"
                    f"- Threat Level: HIGH ({score}%)\n"
                    f"- Weather: {rain} mm rainfall, {wind} km/h wind\n\n"
                    f"ACTION REQUIRED:\n"
                    f"- Avoid low-lying roads and waterlogged intersections.\n"
                    f"- Check your Nirvaha portal for the safest evacuation route and open hospitals.\n\n"
                    f"Stay safe,\nNirvaha Emergency Operations"
                )
                send_alert_email(email, title, email_body, zone=zone_name)

        # 3. SMS alerts to registered citizens in that zone & district emergency lines
        sms_body = f"[NIRVAHA ALERT] HIGH RISK in {zone_name}: {hazard} detected ({score}%). Evacuate to safe zone immediately. Details: nirvaha.gov"
        sent_phones = set()
        for c in citizens:
            phone = c.get("phone")
            if phone and phone not in sent_phones:
                send_sms_alert(phone, sms_body, zone=zone_name)
                sent_phones.add(phone)

        district_phones = [
            f"+91-98800-{zone_name[:4].upper()}-01",
        ]
        for phone in district_phones:
            if phone not in sent_phones:
                send_sms_alert(phone, sms_body, zone=zone_name)
                sent_phones.add(phone)

        logger.info(
            "Dispatched high-risk multi-channel alert for %s to %d citizens (sent to %d phones).",
            zone_name,
            len(citizens),
            len(sent_phones),
        )

    def dispatch_broadcast(self, title, message, zone=None, channels=None, sender="Command Staff"):
        """Staff-initiated emergency broadcast across selected channels."""
        channels = channels or ["push", "email", "sms"]
        zone_label = zone or "City-Wide (All Zones)"
        results = {"push": 0, "email": 0, "sms": 0}

        # 1. Real-time Push
        if "push" in channels:
            _publish_event("emergency_broadcast", {
                "zone": zone,
                "title": title,
                "message": message,
                "sender": sender,
                "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            })
            log_notification(
                channel="push",
                recipient="all_connected_users",
                title=title,
                message=message,
                zone=zone,
                status="sent",
            )
            results["push"] += 1

        # 2. Email Delivery
        if "email" in channels:
            recipients = get_users_by_zone(zone) if zone else get_all_contactable_users()
            for r in recipients:
                email = r.get("email")
                if email:
                    body = (
                        f"EMERGENCY BROADCAST from {sender}\n"
                        f"Target Zone: {zone_label}\n\n"
                        f"{message}\n\n"
                        f"— Nirvaha Disaster Operations Command"
                    )
                    send_alert_email(email, f"BROADCAST: {title}", body, zone=zone)
                    results["email"] += 1

        # 3. SMS Delivery to registered citizen mobile numbers
        if "sms" in channels:
            sms_text = f"[NIRVAHA BROADCAST] {title}: {message}"
            recipients = get_users_by_zone(zone) if zone else get_all_contactable_users()
            sent_phones = set()
            for r in recipients:
                phone = r.get("phone")
                if phone and phone not in sent_phones:
                    send_sms_alert(phone, sms_text, zone=zone)
                    sent_phones.add(phone)
                    results["sms"] += 1
            if not sent_phones:
                fallback_phone = f"+91-98800-{zone[:4].upper() if zone else 'CITY'}-EMRG"
                send_sms_alert(fallback_phone, sms_text, zone=zone)
                results["sms"] += 1

        return {
            "ok": True,
            "zone": zone_label,
            "channels": channels,
            "dispatched": results,
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }


notification_service = NotificationService()
