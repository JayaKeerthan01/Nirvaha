"""
Email delivery for citizen signup verification.

Matches the simulate-by-default pattern used everywhere else in this
project (api/weather_api.py, api/maps_api.py, agents/citizen_chat_agent.py):
  - If SMTP_HOST/SMTP_PORT/SMTP_USER/SMTP_PASSWORD/SMTP_FROM are all set,
    this actually sends mail via Python's built-in smtplib — no extra
    dependency needed for real delivery.
  - Otherwise, "simulation mode": the code is logged server-side and,
    critically, returned to the caller so app.py can display it directly
    on the verification page with a clear "no email service configured"
    banner. Without this fallback, self-service signup would be dead on
    arrival in the default zero-config setup this project ships with.
"""

import logging
import smtplib
from email.mime.text import MIMEText

from config import Config

logger = logging.getLogger(__name__)


def _smtp_configured():
    return all([Config.SMTP_HOST, Config.SMTP_PORT, Config.SMTP_USER, Config.SMTP_PASSWORD, Config.SMTP_FROM])


def send_verification_email(to_email, name, code):
    """Returns True if a real email was actually sent, False if running in
    simulation mode (the caller should then show the code on-screen)."""
    if not _smtp_configured():
        logger.info("SIMULATED EMAIL to %s — verification code: %s", to_email, code)
        return False

    subject = "Your Nirvaha verification code"
    body = (
        f"Hi {name},\n\n"
        f"Your verification code is: {code}\n\n"
        f"This code expires in {Config.VERIFICATION_CODE_EXPIRY_MINUTES} minutes. "
        f"If you didn't request this, you can ignore this email.\n\n"
        f"— Nirvaha"
    )
    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"] = Config.SMTP_FROM
    msg["To"] = to_email

    try:
        with smtplib.SMTP(Config.SMTP_HOST, Config.SMTP_PORT, timeout=10) as server:
            server.starttls()
            server.login(Config.SMTP_USER, Config.SMTP_PASSWORD)
            server.sendmail(Config.SMTP_FROM, [to_email], msg.as_string())
        return True
    except Exception as exc:
        logger.warning("Real SMTP send failed for %s (%s); falling back to simulation", to_email, exc)
        logger.info("SIMULATED EMAIL to %s — verification code: %s", to_email, code)
        return False
