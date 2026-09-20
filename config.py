"""
Central configuration for the Disaster Response System.

All tunable values live here so the rest of the codebase never hard-codes
paths, keys, or thresholds.
"""

import os

BASE_DIR = os.path.abspath(os.path.dirname(__file__))

# Automatically load environment variables from .env file if present
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(BASE_DIR, ".env"))
except ImportError:
    pass

DEFAULT_SECRET_KEY = "dev-secret-key-change-in-production"


class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY", DEFAULT_SECRET_KEY)

    # Was hardcoded `debug=True` in app.py, which leaves the interactive
    # Werkzeug debugger (arbitrary code execution) reachable if this is ever
    # exposed beyond localhost. Now off unless explicitly enabled.
    DEBUG = os.environ.get("FLASK_DEBUG", "0") == "1"

    # Hardened session cookies. SECURE is left off by default because the
    # dev server runs on plain http://127.0.0.1 — set SESSION_COOKIE_SECURE=1
    # once this is behind HTTPS (e.g. a reverse proxy in production).
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = os.environ.get("SESSION_COOKIE_SECURE", "0") == "1"

    # Login brute-force protection: lock out an IP after this many failed
    # attempts within the window. Previously /login had no rate limiting at
    # all against a documented demo password.
    LOGIN_MAX_FAILED_ATTEMPTS = 5
    LOGIN_LOCKOUT_WINDOW_MINUTES = 15

    # SQLite database (swap for MySQL/PostgreSQL in production by editing database/db.py)
    DATABASE_PATH = os.path.join(BASE_DIR, "database", "disaster_response.db")

    # Trained ML model artifacts
    MODELS_DIR = os.path.join(BASE_DIR, "models")
    DISASTER_MODEL_PATH = os.path.join(MODELS_DIR, "disaster_model.joblib")
    TRAFFIC_MODEL_PATH = os.path.join(MODELS_DIR, "traffic_model.joblib")

    # Synthetic training data (replace with real historical records when available)
    DATASETS_DIR = os.path.join(BASE_DIR, "datasets")
    WEATHER_HISTORY_CSV = os.path.join(DATASETS_DIR, "weather_history.csv")
    TRAFFIC_HISTORY_CSV = os.path.join(DATASETS_DIR, "traffic_history.csv")

    # External API keys. Leave blank to run in SIMULATION MODE, where the
    # Weather/Maps agents generate realistic synthetic data instead of
    # calling the real internet APIs. Fill these in to go live.
    OPENWEATHER_API_KEY = os.environ.get("OPENWEATHER_API_KEY", "")
    GOOGLE_MAPS_API_KEY = os.environ.get("GOOGLE_MAPS_API_KEY", "")

    # Optional: powers a more natural free-form citizen chatbot via Claude.
    # Leave blank and the chatbot still works, using a zero-dependency
    # keyword/intent matcher over the same live data instead — same
    # simulate-by-default pattern as the two keys above.
    ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
    ANTHROPIC_CHAT_MODEL = os.environ.get("ANTHROPIC_CHAT_MODEL", "claude-sonnet-4-6")

    # Email verification for citizen signup and disaster alert delivery.
    SMTP_HOST = os.environ.get("SMTP_HOST", "")
    SMTP_PORT = int(os.environ.get("SMTP_PORT", "587") or 587)
    SMTP_USER = os.environ.get("SMTP_USER", "")
    SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD", "").replace(" ", "")
    SMTP_FROM = os.environ.get("SMTP_FROM", os.environ.get("SMTP_USER", ""))
    VERIFICATION_CODE_EXPIRY_MINUTES = 15

    # A citizen counts as "currently active" on the admin dashboard if
    # they've made an authenticated request within this window.
    ACTIVE_USER_WINDOW_MINUTES = 5

    # Default map center (HSR Layout, Bangalore) — change to your region
    DEFAULT_LAT = 12.9121
    DEFAULT_LON = 77.6446
    DEFAULT_CITY = "HSR Layout, Bangalore"

    # Seed data for the `zones` DB table on first run only. After that,
    # zones are managed at runtime (add/remove from /admin/zones as an
    # admin user) rather than requiring a code change — every agent, the
    # map, and hospital/rescue seed data read zones via database.db.get_zones().
    DEFAULT_ZONES = [
        {"name": "HSR Layout", "lat": 12.9082, "lon": 77.6476, "density": 0.90},
        {"name": "Koramangala", "lat": 12.9352, "lon": 77.6245, "density": 0.95},
        {"name": "Bellandur", "lat": 12.9304, "lon": 77.6784, "density": 0.75},
        {"name": "BTM Layout", "lat": 12.9166, "lon": 77.6101, "density": 0.85},
        {"name": "Electronic City", "lat": 12.8452, "lon": 77.6602, "density": 0.65},
    ]

    # How often (seconds) the frontend polls the live-data endpoints
    POLL_INTERVAL_MS = 15000

    # Estimated hospital beds to reserve per zone deployment (see
    # agents/hospital_agent.py::reserve_for_deployment). A simplification —
    # a real system would size this from an actual casualty estimate.
    BEDS_RESERVED_PER_DEPLOYMENT = 5

    # SMS / Emergency Alert configuration.
    # 1. Fast2SMS (Recommended for Indian mobile numbers - free trial credits, no DLT fee)
    FAST2SMS_API_KEY = os.environ.get("FAST2SMS_API_KEY", "")

    # 2. Twilio (International SMS & WhatsApp Sandbox delivery)
    TWILIO_ACCOUNT_SID = os.environ.get("TWILIO_ACCOUNT_SID", "")
    TWILIO_AUTH_TOKEN = os.environ.get("TWILIO_AUTH_TOKEN", "")
    TWILIO_PHONE_NUMBER = os.environ.get("TWILIO_PHONE_NUMBER", "")
    TWILIO_WHATSAPP_NUMBER = os.environ.get("TWILIO_WHATSAPP_NUMBER", "+14155238886")

    # Autonomous rescue & hospital auto-dispatch toggle default
    AUTO_DISPATCH_DEFAULT = os.environ.get("AUTO_DISPATCH_ENABLED", "0") == "1"

    # Security & Session Hardening
    SESSION_IDLE_TIMEOUT_MINUTES = 30
    PASSWORD_RESET_EXPIRY_MINUTES = 15

