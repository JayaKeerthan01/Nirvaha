"""
Intelligent Multi-Agent Disaster Response and Resource Management System
--------------------------------------------------------------------------
Flask entry point. Wires together the database, the four specialized agents,
and the Coordinator Agent, and serves the dashboard frontend.

Run:
    python app.py
Then open:
    http://127.0.0.1:5000
Demo login (admin):
    email:    admin@disaster-response.local
    password: admin123
Demo login (sub admin, no zone-management access):
    email:    subadmin@disaster-response.local
    password: subadmin123
Demo login (citizen — public portal, /citizen):
    email:    resident@disaster-response.local
    password: resident123

Changelog (hardening pass) — see README.md "Changelog" section for the
full write-up. Summary of what changed in this file specifically:
  - debug=True was hardcoded, which leaves the interactive Werkzeug
    debugger (arbitrary code execution) reachable if ever exposed beyond
    localhost. Now controlled by Config.DEBUG (FLASK_DEBUG env var).
  - No CSRF protection existed on POST requests. Added a session-bound
    token, generated via csrf_token() in templates and validated in
    csrf_protect() below.
  - `role` existed on the users table but was never enforced anywhere.
    Added admin_required() and used it on the new zone-management routes.
  - /login had no rate limiting. Added a per-IP failed-attempt counter
    backed by the login_attempts table.
  - Added /api/deploy, /api/recall (turn a recommendation into a real,
    reversible state change) and /admin/zones (dynamic zone management,
    previously required editing Config.ZONES and redeploying).
  - Added a public-facing citizen portal (/citizen/*): self-service
    signup, a plain-language risk/evacuation/hospital view, and a chatbot
    (agents/citizen_chat_agent.py) — separate from the operator/admin
    command dashboard and gated only by login_required, not admin_required.
"""

import logging
import secrets
import hashlib
import re
from functools import wraps
from datetime import datetime, timedelta

import csv
import io
import json
import time

from flask import (
    Flask, render_template, redirect, url_for, session, request,
    jsonify, flash, abort, Response,
)
from werkzeug.security import check_password_hash, generate_password_hash

from config import Config, DEFAULT_SECRET_KEY
from database.db import (
    init_db, query, get_zones, add_zone, delete_zone,
    get_recent_disasters, record_login_attempt, count_recent_failed_attempts,
    log_alert, get_user_by_email, get_user_by_id, create_citizen,
    set_verification_code, check_verification_code, mark_email_verified,
    touch_last_seen, citizen_account_stats, get_recent_citizens,
    get_recent_notifications, is_auto_dispatch_enabled, set_auto_dispatch_enabled,
    log_audit, get_audit_logs, get_shelters,
    bulk_insert_zones, bulk_insert_hospitals, bulk_insert_teams,
    create_password_reset_code, verify_password_reset_code, consume_password_reset,
    get_all_iot_sensors, update_iot_sensor_reading,
    get_social_distress_feed, log_social_distress,
    create_emergency_report, get_emergency_reports, update_emergency_report_status,
)
from agents.weather_agent import weather_agent
from agents.traffic_agent import traffic_agent
from agents.hospital_agent import hospital_agent
from agents.rescue_agent import rescue_agent
from agents.coordinator_agent import coordinator_agent
from agents.citizen_chat_agent import answer as chat_answer
from agents.auto_assignment_engine import auto_assignment_engine
from agents.satellite_agent import satellite_agent
from agents.iot_agent import iot_agent
from agents.social_media_agent import social_media_agent
from api.email_api import send_verification_email
from api.notification_service import notification_service, subscribe, unsubscribe, get_recent_live_events
from utils.email_validator import is_valid_email, validate_email_for_signup, EMAIL_STRICT_RE


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.config.from_object(Config)

if not Config.DEBUG and Config.SECRET_KEY == DEFAULT_SECRET_KEY:
    logger.warning(
        "SECURITY WARNING: running with the default SECRET_KEY outside debug "
        "mode. Set the SECRET_KEY environment variable before deploying "
        "this anywhere reachable by other people."
    )


# ------------------------------------------------------------- security ----

def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("user_id"):
            return redirect(url_for("login", next=request.path))
        return view(*args, **kwargs)
    return wrapped


def admin_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("user_id"):
            return redirect(url_for("login", next=request.path))
        if session.get("role") != "admin":
            flash("That page requires an administrator account.", "error")
            return redirect(url_for("dashboard"))
        return view(*args, **kwargs)
    return wrapped


def staff_required(view):
    """Admin or operator — i.e. anyone but a citizen account. Guards the
    entire ops command center (dashboard, rescue, deploy/recall, alerts)
    now that /signup lets the public create accounts. Without this, a
    self-registered citizen would have had the same power to dispatch real
    rescue teams as an admin — login_required alone isn't enough once
    account creation is public."""
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("user_id"):
            return redirect(url_for("login", next=request.path))
        if session.get("role") not in ("admin", "operator"):
            flash("That page is for response-team accounts. Try the citizen portal instead.", "error")
            return redirect(url_for("citizen_home"))
        return view(*args, **kwargs)
    return wrapped


def get_csrf_token():
    if "csrf_token" not in session:
        session["csrf_token"] = secrets.token_hex(32)
    return session["csrf_token"]


app.jinja_env.globals["csrf_token"] = get_csrf_token


@app.before_request
def csrf_protect():
    if request.method != "POST":
        return
    # Microcontroller hardware (ESP32/Arduino) & automated external social feeds
    # send telemetry payloads without browser cookie sessions
    if request.path in ("/api/iot/telemetry", "/api/social/distress", "/api/social/sync"):
        return
    submitted = request.form.get("csrf_token") or request.headers.get("X-CSRFToken")
    if not submitted and request.is_json:
        submitted = (request.get_json(silent=True) or {}).get("csrf_token")
    expected = session.get("csrf_token", "")
    if not submitted or not expected or not secrets.compare_digest(str(submitted), str(expected)):
        abort(400, description="Missing or invalid CSRF token. Reload the page and try again.")


@app.before_request
def touch_activity():
    """Updates users.last_seen on every authenticated request and enforces
    Config.SESSION_IDLE_TIMEOUT_MINUTES (30 min) idle timeout. Also ensures
    unverified users cannot access authenticated routes."""
    user_id = session.get("user_id")
    if user_id:
        if request.endpoint not in ("login", "logout", "static", "forgot_password", "reset_password", "verify_email", "resend_code"):
            last_activity = session.get("last_activity")
            now_ts = datetime.utcnow().timestamp()
            if last_activity and (now_ts - float(last_activity) > Config.SESSION_IDLE_TIMEOUT_MINUTES * 60):
                session.clear()
                flash("Your session timed out due to 30 minutes of inactivity. Please sign in again.", "error")
                return redirect(url_for("login"))
            user = get_user_by_id(user_id)
            if user and not user["email_verified"]:
                session.clear()
                flash("Please verify your email address before signing in.", "error")
                return redirect(url_for("login"))
            session["last_activity"] = now_ts
        touch_last_seen(user_id)


PHONE_DIGITS_RE = re.compile(r"^\+?[0-9\s\-]{10,18}$")


def is_valid_phone(phone: str) -> bool:
    """Validates mobile phone number format: 10 to 15 digits, optional leading +."""
    if not phone:
        return False
    cleaned = phone.strip()
    if not PHONE_DIGITS_RE.match(cleaned):
        return False
    digits = re.sub(r"[^\d]", "", cleaned)
    return 10 <= len(digits) <= 15


def normalize_phone(phone: str) -> str:
    """Standardizes phone format with preserved international + prefix if provided."""
    raw = phone.strip()
    has_plus = raw.startswith("+")
    digits = re.sub(r"[^\d]", "", raw)
    return f"+{digits}" if has_plus else digits


EMAIL_RE = EMAIL_STRICT_RE


def hash_code(code):
    return hashlib.sha256(code.encode()).hexdigest()


def issue_verification_code(user_id, email, name):
    """Generates a fresh 6-digit code, stores its hash, and attempts to
    email it. Returns the plaintext code ONLY when running in simulation
    mode (no SMTP configured) so the caller can display it on-screen —
    never returned when a real email was actually sent."""
    code = f"{secrets.randbelow(1_000_000):06d}"
    expires_at = (datetime.utcnow() + timedelta(minutes=Config.VERIFICATION_CODE_EXPIRY_MINUTES)).strftime("%Y-%m-%d %H:%M:%S")
    set_verification_code(user_id, hash_code(code), expires_at)
    really_sent = send_verification_email(email, name, code)
    return None if really_sent else code


@app.context_processor
def inject_globals():
    return {
        "app_name": "Nirvaha",
        "poll_interval_ms": Config.POLL_INTERVAL_MS,
        "current_user": session.get("user_name"),
        "current_role": session.get("role"),
    }


# ---------------------------------------------------------------- PAGES ----

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        ip = request.remote_addr or "unknown"
        failed_recently = count_recent_failed_attempts(ip, Config.LOGIN_LOCKOUT_WINDOW_MINUTES)

        if failed_recently >= Config.LOGIN_MAX_FAILED_ATTEMPTS:
            flash(
                f"Too many failed sign-in attempts. Try again in "
                f"{Config.LOGIN_LOCKOUT_WINDOW_MINUTES} minutes.", "error",
            )
            return render_template("login.html")

        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        # Only users with a valid email syntax can attempt login
        if not email or not is_valid_email(email):
            flash("Please enter a valid email address.", "error")
            return render_template("login.html", email=email)

        user = query("SELECT * FROM users WHERE email = ?", (email,), fetchone=True)
        success = bool(user and check_password_hash(user["password_hash"], password))
        record_login_attempt(ip, email, success)

        if success:
            # Only users with verified email can log in
            if not user["email_verified"]:
                log_audit(user["id"], user["name"], user["role"], "LOGIN_BLOCKED_UNVERIFIED", "user", str(user["id"]), "Sign-in blocked: email not verified", ip)
                dev_code = issue_verification_code(user["id"], user["email"], user["name"])
                flash("Your email address is not verified yet. We have sent a 6-digit verification code to your email. Please verify your email before signing in.", "error")
                return redirect(url_for("verify_email", email=email, dev_code=dev_code) if dev_code else url_for("verify_email", email=email))

            log_audit(user["id"], user["name"], user["role"], "LOGIN_SUCCESS", "user", str(user["id"]), "User signed in successfully", ip)
            session["user_id"] = user["id"]
            session["user_name"] = user["name"]
            session["role"] = user["role"]
            session["zone"] = user["zone"]
            session["last_activity"] = datetime.utcnow().timestamp()
            default_next = url_for("citizen_home") if user["role"] == "citizen" else url_for("dashboard")
            return redirect(request.args.get("next") or default_next)

        log_audit(None, email or "anonymous", None, "LOGIN_FAILED", "auth", email, "Failed sign-in attempt", ip)
        flash("Invalid email or password.", "error")
        return render_template("login.html", email=email)
    return render_template("login.html")


@app.route("/signup", methods=["GET", "POST"])
def signup():
    """Public self-service signup — always creates a 'citizen' role
    account, unverified. There is no path from this form to admin/operator;
    those are only ever created by seeding or directly in the database.
    Does NOT log the person in — see /verify-email for that."""
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        phone = request.form.get("phone", "").strip()
        password = request.form.get("password", "")
        zone = request.form.get("zone") or None

        errors = []
        if not name:
            errors.append("Name is required.")

        # Multi-layer anti-fake email verification (RFC syntax, disposable domains, bogus domains, DNS/MX check)
        email_ok, email_err = validate_email_for_signup(email, is_testing=app.config.get("TESTING", False))
        if not email_ok:
            errors.append(email_err)

        if not phone or not is_valid_phone(phone):
            errors.append("A valid 10 to 15-digit mobile number is required to receive emergency SMS alerts.")
        if len(password) < 8:
            errors.append("Password must be at least 8 characters.")
        if not errors and get_user_by_email(email):
            errors.append("An account with that email already exists.")

        if errors:
            for e in errors:
                flash(e, "error")
            return render_template("signup.html", zones=get_zones(), name=name, email=email, phone=phone, zone=zone)

        clean_phone = normalize_phone(phone)
        user_id = create_citizen(name, email, generate_password_hash(password), zone=zone, phone=clean_phone)
        dev_code = issue_verification_code(user_id, email, name)
        flash(f"Almost done, {name} — enter the verification code we sent to {email}.", "success")
        return redirect(url_for("verify_email", email=email, dev_code=dev_code) if dev_code else url_for("verify_email", email=email))

    return render_template("signup.html", zones=get_zones())


@app.route("/verify-email", methods=["GET", "POST"])
def verify_email():
    email = (request.args.get("email") or request.form.get("email") or "").strip().lower()
    dev_code = request.args.get("dev_code")  # simulation-mode only, never set for a real send

    if request.method == "POST":
        user = get_user_by_email(email)
        submitted = "".join(request.form.get(f"digit{i}", "") for i in range(1, 7)) or request.form.get("code", "").strip()

        if not user or user["role"] != "citizen":
            flash("We couldn't find that account.", "error")
        elif user["email_verified"]:
            flash("That email is already verified — you can sign in.", "success")
            return redirect(url_for("login"))
        elif check_verification_code(user["id"], hash_code(submitted)):
            mark_email_verified(user["id"])
            session["user_id"] = user["id"]
            session["user_name"] = user["name"]
            session["role"] = "citizen"
            session["zone"] = user["zone"]
            flash("Email verified — welcome to Nirvaha.", "success")
            return redirect(url_for("citizen_home"))
        else:
            flash("That code is incorrect or has expired. Try again or resend it.", "error")

    return render_template("verify_email.html", email=email, dev_code=dev_code)


@app.route("/resend-code", methods=["POST"])
def resend_code():
    email = (request.form.get("email") or "").strip().lower()
    user = get_user_by_email(email)
    if not user or user["role"] != "citizen":
        flash("We couldn't find that account.", "error")
        return redirect(url_for("signup"))
    if user["email_verified"]:
        flash("That email is already verified — you can sign in.", "success")
        return redirect(url_for("login"))

    dev_code = issue_verification_code(user["id"], email, user["name"])
    flash("A new code has been sent.", "success")
    return redirect(url_for("verify_email", email=email, dev_code=dev_code) if dev_code else url_for("verify_email", email=email))


@app.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        user = get_user_by_email(email)
        if not user:
            flash("If that email is registered, a password reset code has been issued.", "info")
            return redirect(url_for("login"))

        code = f"{secrets.randbelow(1_000_000):06d}"
        expires_at = (datetime.utcnow() + timedelta(minutes=Config.PASSWORD_RESET_EXPIRY_MINUTES)).strftime("%Y-%m-%d %H:%M:%S")
        create_password_reset_code(user["id"], hash_code(code), expires_at)
        really_sent = send_verification_email(email, user["name"], code)
        dev_code = None if really_sent else code

        log_audit(user["id"], user["name"], user["role"], "PASSWORD_RESET_REQUEST", "user", str(user["id"]), f"Password reset requested for {email}", request.remote_addr)
        flash("A 6-digit password reset code has been sent to your email.", "success")
        return redirect(url_for("reset_password", email=email, dev_code=dev_code) if dev_code else url_for("reset_password", email=email))

    return render_template("forgot_password.html")


@app.route("/reset-password", methods=["GET", "POST"])
def reset_password():
    email = (request.args.get("email") or request.form.get("email") or "").strip().lower()
    dev_code = request.args.get("dev_code")

    if request.method == "POST":
        code = request.form.get("code", "").strip()
        new_password = request.form.get("new_password", "")
        confirm_password = request.form.get("confirm_password", "")

        user = get_user_by_email(email)
        if not user:
            flash("Account not found.", "error")
            return redirect(url_for("forgot_password"))

        if len(new_password) < 8:
            flash("New password must be at least 8 characters long.", "error")
            return render_template("reset_password.html", email=email, dev_code=dev_code)

        if new_password != confirm_password:
            flash("Passwords do not match.", "error")
            return render_template("reset_password.html", email=email, dev_code=dev_code)

        if not verify_password_reset_code(user["id"], hash_code(code)):
            flash("The reset code is invalid or has expired.", "error")
            return render_template("reset_password.html", email=email, dev_code=dev_code)

        consume_password_reset(user["id"], generate_password_hash(new_password))
        log_audit(user["id"], user["name"], user["role"], "PASSWORD_RESET_COMPLETE", "user", str(user["id"]), f"Password successfully updated for {email}", request.remote_addr)
        flash("Password successfully reset! Please sign in with your new credentials.", "success")
        return redirect(url_for("login"))

    return render_template("reset_password.html", email=email, dev_code=dev_code)


@app.route("/logout")
def logout():
    uid = session.get("user_id")
    uname = session.get("user_name")
    urole = session.get("role")
    if uid:
        log_audit(uid, uname, urole, "LOGOUT", "user", str(uid), "User logged out", request.remote_addr)
    session.clear()
    return redirect(url_for("index"))


@app.route("/dashboard")
@staff_required
def dashboard():
    return render_template("dashboard.html", zones=get_zones())


@app.route("/predictions")
@staff_required
def predictions():
    return render_template("predictions.html", zones=get_zones())


@app.route("/traffic")
@staff_required
def traffic():
    return render_template("traffic.html", zones=get_zones())


@app.route("/hospitals")
@staff_required
def hospitals():
    return render_template("hospitals.html", zones=get_zones())


@app.route("/rescue")
@staff_required
def rescue():
    return render_template("rescue.html", zones=get_zones())


@app.route("/alerts")
@staff_required
def alerts():
    return render_template("alerts.html")


@app.route("/admin/zones", methods=["GET", "POST"])
@admin_required
def admin_zones():
    if request.method == "POST":
        try:
            name = request.form["name"].strip()
            lat = float(request.form["lat"])
            lon = float(request.form["lon"])
            density = float(request.form.get("density", 0.5))
            if not name:
                raise ValueError("Zone name is required.")
            if not (-90 <= lat <= 90 and -180 <= lon <= 180):
                raise ValueError("Latitude/longitude out of range.")
            if not (0 <= density <= 1):
                raise ValueError("Density must be between 0 and 1.")
            add_zone(name, lat, lon, density)
            log_audit(session.get("user_id"), session.get("user_name"), session.get("role"), "ADD_ZONE", "zone", name, f"Added zone '{name}' (lat={lat}, lon={lon}, density={density})", request.remote_addr)
            flash(f"Zone '{name}' added.", "success")
        except Exception as exc:
            flash(f"Could not add zone: {exc}", "error")
        return redirect(url_for("admin_zones"))

    return render_template("admin_zones.html", zones=get_zones())


@app.route("/admin/zones/<int:zone_id>/delete", methods=["POST"])
@admin_required
def admin_delete_zone(zone_id):
    delete_zone(zone_id)
    log_audit(session.get("user_id"), session.get("user_name"), session.get("role"), "DELETE_ZONE", "zone", str(zone_id), f"Removed zone ID {zone_id}", request.remote_addr)
    flash("Zone removed.", "success")
    return redirect(url_for("admin_zones"))


@app.route("/admin/users")
@admin_required
def admin_users():
    return render_template(
        "admin_users.html",
        stats=citizen_account_stats(),
        citizens=get_recent_citizens(),
        active_window=Config.ACTIVE_USER_WINDOW_MINUTES,
    )


@app.route("/api/admin/users")
@admin_required
def api_admin_users():
    return jsonify({
        "stats": citizen_account_stats(),
        "citizens": get_recent_citizens(),
    })


# -------------------------------------------------------- Bulk Data Import ----

@app.route("/admin/import", methods=["GET", "POST"])
@admin_required
def admin_import():
    import_results = None
    if request.method == "POST":
        entity_type = request.form.get("entity_type", "zones")
        csv_file = request.files.get("csv_file")
        csv_text = request.form.get("csv_text", "").strip()

        content = ""
        if csv_file and csv_file.filename:
            content = csv_file.read().decode("utf-8", errors="replace")
        elif csv_text:
            content = csv_text

        if not content:
            flash("Please provide CSV content either by file upload or pasting into the text area.", "error")
            return redirect(url_for("admin_import"))

        try:
            reader = csv.DictReader(io.StringIO(content))
            rows = [dict(row) for row in reader]

            if entity_type == "zones":
                import_results = bulk_insert_zones(rows)
            elif entity_type == "hospitals":
                import_results = bulk_insert_hospitals(rows)
            elif entity_type == "teams":
                import_results = bulk_insert_teams(rows)
            else:
                flash(f"Unknown entity type: {entity_type}", "error")
                return redirect(url_for("admin_import"))

            log_audit(
                session.get("user_id"),
                session.get("user_name"),
                session.get("role"),
                "BULK_IMPORT",
                entity_type,
                None,
                f"Ingested {import_results['imported']} {entity_type} records with {len(import_results['errors'])} errors",
                request.remote_addr,
            )
            flash(f"Bulk ingestion complete: {import_results['imported']} {entity_type} records processed successfully.", "success")
        except Exception as e:
            flash(f"Failed to parse CSV: {e}", "error")

    total_zones = len(get_zones())
    total_hospitals = len(hospital_agent.get_all_hospitals())
    total_teams = len(rescue_agent.get_all_teams())

    return render_template(
        "admin_import.html",
        total_zones=total_zones,
        total_hospitals=total_hospitals,
        total_teams=total_teams,
        import_results=import_results,
    )


@app.route("/admin/import/sample/<entity_type>")
@admin_required
def admin_import_sample(entity_type):
    samples = {
        "zones": "name,lat,lon,density\nIndiranagar,12.9784,77.6408,0.78\nKoramangala,12.9352,77.6245,0.85\n",
        "hospitals": "hospital_name,location,lat,lon,beds_total,beds_available,doctors_available,ambulances_available,icu_available,contact\nMetro Life Hospital,Indiranagar,12.9750,77.6380,120,35,18,5,8,080-25251234\n",
        "teams": "team_name,zone,lat,lon,vehicles,ambulances,fire_units,personnel,status\nRapid Rescue Alpha,Indiranagar,12.9760,77.6390,5,3,2,20,available\n",
    }
    content = samples.get(entity_type, "id,name\n")
    return Response(
        content,
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename=sample_{entity_type}.csv"},
    )


# ------------------------------------------------------------ Audit Trail ----

@app.route("/admin/audit")
@admin_required
def admin_audit():
    action = request.args.get("action") or None
    role = request.args.get("role") or None
    logs = get_audit_logs(limit=150, action=action, role=role)
    all_logs = get_audit_logs(limit=1000)
    return render_template(
        "admin_audit.html",
        logs=logs,
        total_count=len(all_logs),
        selected_action=action or "",
        selected_role=role or "",
    )


@app.route("/api/admin/audit")
@admin_required
def api_admin_audit():
    action = request.args.get("action") or None
    role = request.args.get("role") or None
    return jsonify(get_audit_logs(limit=100, action=action, role=role))


# ------------------------------------------------- Reporting & Analytics ----

@app.route("/reports")
@staff_required
def reports():
    period = request.args.get("period", "daily")
    now_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")

    if period == "daily":
        date_filter = "datetime(date) >= datetime('now', '-1 day')"
        notif_filter = "datetime(created_at) >= datetime('now', '-1 day')"
    else:
        date_filter = "datetime(date) >= datetime('now', '-30 day')"
        notif_filter = "datetime(created_at) >= datetime('now', '-30 day')"

    incidents = query(f"SELECT * FROM disasters WHERE {date_filter} ORDER BY id DESC LIMIT 100")
    deployments = query(
        """SELECT d.*, t.team_name, h.hospital_name
           FROM deployments d
           LEFT JOIN rescue_teams t ON d.team_id = t.id
           LEFT JOIN hospitals h ON d.hospital_id = h.id
           ORDER BY d.id DESC LIMIT 100"""
    )
    all_deployments = query("SELECT * FROM deployments")
    active_deployments = [d for d in all_deployments if d["status"] == "active"]

    total_notifications = query(f"SELECT COUNT(*) as c FROM notifications WHERE {notif_filter}", fetchone=True)["c"]
    high_severity_count = sum(1 for inc in incidents if inc["severity"] == "High")

    stats = {
        "total_incidents": len(incidents),
        "high_severity_count": high_severity_count,
        "total_deployments": len(all_deployments),
        "active_deployments": len(active_deployments),
        "total_notifications": total_notifications,
    }

    return render_template(
        "reports.html",
        period=period,
        stats=stats,
        incidents=incidents,
        deployments=deployments,
        now_str=now_str,
    )


@app.route("/reports/export/incidents.csv")
@staff_required
def reports_export_incidents():
    incidents = query("SELECT * FROM disasters ORDER BY id DESC LIMIT 500")
    si = io.StringIO()
    cw = csv.writer(si)
    cw.writerow(["ID", "Date", "Location", "Disaster Type", "Severity", "Probability", "Prediction", "Lat", "Lon"])
    for inc in incidents:
        cw.writerow([
            inc["id"], inc["date"], inc["location"], inc["disaster_type"],
            inc["severity"], inc["probability"], inc["prediction"], inc["lat"], inc["lon"],
        ])
    return Response(
        si.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=incidents_export.csv"},
    )


@app.route("/reports/export/deployments.csv")
@staff_required
def reports_export_deployments():
    deployments = query(
        """SELECT d.*, t.team_name, h.hospital_name
           FROM deployments d
           LEFT JOIN rescue_teams t ON d.team_id = t.id
           LEFT JOIN hospitals h ON d.hospital_id = h.id
           ORDER BY d.id DESC LIMIT 500"""
    )
    si = io.StringIO()
    cw = csv.writer(si)
    cw.writerow(["ID", "Team ID", "Team Name", "Zone", "Hospital ID", "Hospital Name", "Beds Reserved", "Deployed By", "Status", "Deployed At", "Recalled At"])
    for dep in deployments:
        cw.writerow([
            dep["id"], dep["team_id"], dep["team_name"], dep["zone"],
            dep["hospital_id"], dep["hospital_name"], dep["beds_reserved"],
            dep["deployed_by"], dep["status"], dep["deployed_at"], dep["recalled_at"],
        ])
    return Response(
        si.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=deployments_export.csv"},
    )


@app.route("/api/reports/summary")
@staff_required
def api_reports_summary():
    incidents_today = query("SELECT COUNT(*) as c FROM disasters WHERE datetime(date) >= datetime('now', '-1 day')", fetchone=True)["c"]
    incidents_monthly = query("SELECT COUNT(*) as c FROM disasters WHERE datetime(date) >= datetime('now', '-30 day')", fetchone=True)["c"]
    high_today = query("SELECT COUNT(*) as c FROM disasters WHERE severity='High' AND datetime(date) >= datetime('now', '-1 day')", fetchone=True)["c"]
    deployments = query("SELECT COUNT(*) as total, SUM(CASE WHEN status='active' THEN 1 ELSE 0 END) as active FROM deployments", fetchone=True)
    notifs = query("SELECT COUNT(*) as c FROM notifications WHERE datetime(created_at) >= datetime('now', '-1 day')", fetchone=True)["c"]

    return jsonify({
        "incidents_today": incidents_today,
        "incidents_monthly": incidents_monthly,
        "high_severity_today": high_today,
        "total_deployments": deployments["total"] or 0,
        "active_deployments": deployments["active"] or 0,
        "notifications_24h": notifs,
    })



# --------------------------------------------------------- citizen pages ---
# Public-facing pages: any logged-in account can view these (not just the
# 'citizen' role) — there's nothing sensitive here, just the same read-only
# risk/route/hospital data the ops dashboard shows, presented simply.

@app.route("/citizen")
@login_required
def citizen_home():
    return render_template("citizen_home.html", zones=get_zones(), home_zone=session.get("zone"))


@app.route("/citizen/evacuation")
@login_required
def citizen_evacuation():
    return render_template("citizen_evacuation.html", zones=get_zones(), home_zone=session.get("zone"))


@app.route("/citizen/hospitals")
@login_required
def citizen_hospitals():
    return render_template("citizen_hospitals.html", zones=get_zones(), home_zone=session.get("zone"))


@app.route("/citizen/chat")
@login_required
def citizen_chat():
    return render_template("citizen_chat.html", home_zone=session.get("zone"))


@app.route("/api/citizen/chat", methods=["POST"])
@login_required
def api_citizen_chat():
    body = request.get_json(silent=True) or {}
    question = (body.get("question") or "").strip()
    if not question:
        return jsonify({"ok": False, "error": "question is required"}), 400
    if len(question) > 500:
        return jsonify({"ok": False, "error": "Keep questions under 500 characters."}), 400
    result = chat_answer(question)
    return jsonify({"ok": True, **result})


# ------------------------------------------------------------- JSON API ----

@app.route("/api/dashboard")
@staff_required
def api_dashboard():
    return jsonify(coordinator_agent.full_report())


@app.route("/api/weather")
@login_required
def api_weather():
    return jsonify(weather_agent.assess_all_zones())


@app.route("/api/traffic")
@staff_required
def api_traffic():
    weather_by_zone = {w["zone"]: w for w in weather_agent.assess_all_zones()}
    return jsonify(traffic_agent.assess_all_zones(weather_by_zone))


@app.route("/api/hospitals")
@login_required
def api_hospitals():
    """Two modes in one endpoint:
      - No ?zone= param: every hospital, annotated with accessibility
        status but not ranked or filtered. This is what the ops Hospitals
        page uses — admin/sub admin need to see the full picture, not a
        pre-filtered subset, especially when deciding where to route
        overflow from a compromised area.
      - ?zone=<name>: the top nearby, ranked-by-suitability hospitals for
        that specific zone (same accessibility-aware ranking used by the
        dashboard and the citizen chatbot). This is what the citizen
        Hospitals page uses — someone in a disaster doesn't need a list
        of all 15 hospitals across the city, they need the closest ones
        that are actually reachable.
    """
    weather_by_zone = {w["zone"]: w for w in weather_agent.assess_all_zones()}
    zone_name = request.args.get("zone")
    if zone_name:
        zones_by_name = {z["name"]: z for z in get_zones()}
        zone_obj = zones_by_name.get(zone_name)
        if zone_obj:
            return jsonify(hospital_agent.recommend_for_zone(zone_obj, top_n=6, weather_by_zone=weather_by_zone))
    return jsonify(hospital_agent.get_all_hospitals_with_status(weather_by_zone))


@app.route("/api/rescue")
@staff_required
def api_rescue():
    weather_assessments = weather_agent.assess_all_zones()
    return jsonify(
        {
            "summary": rescue_agent.status_summary(),
            "priorities": rescue_agent.prioritize_zones(weather_assessments),
            "deployment": rescue_agent.recommend_deployment(weather_assessments),
            "active_deployments": rescue_agent.get_active_deployments(),
            "teams": rescue_agent.get_all_teams(),
        }
    )


@app.route("/api/alerts")
@staff_required
def api_alerts():
    rows = query("SELECT * FROM alerts ORDER BY id DESC LIMIT 50")
    return jsonify(rows)


@app.route("/api/incidents")
@staff_required
def api_incidents():
    """Full disaster-event history, now that weather_agent actually writes
    to the `disasters` table on every assessment instead of that table
    sitting unused."""
    return jsonify(get_recent_disasters(100))


@app.route("/api/route")
@login_required
def api_route():
    zones = get_zones()
    from_zone_name = request.args.get("from", zones[0]["name"] if zones else None)
    weather_by_zone = {w["zone"]: w for w in weather_agent.assess_all_zones()}
    traffic_assessments = traffic_agent.assess_all_zones(weather_by_zone)
    route = traffic_agent.safest_route(from_zone_name, traffic_assessments, risk_by_zone=weather_by_zone)
    return jsonify(route)


@app.route("/api/citizen/shelter")
@login_required
def api_citizen_shelter():
    zones = get_zones()
    from_zone = request.args.get("zone") or session.get("zone") or (zones[0]["name"] if zones else None)
    if not from_zone:
        return jsonify({"ok": False, "error": "No zones found"}), 404

    weather_by_zone = {w["zone"]: w for w in weather_agent.assess_all_zones()}
    traffic_assessments = traffic_agent.assess_all_zones(weather_by_zone)
    route = traffic_agent.safe_shelter_route(from_zone, risk_by_zone=weather_by_zone, traffic_assessments=traffic_assessments)
    if not route:
        return jsonify({"ok": False, "error": "No relief shelter found"}), 404
    return jsonify(route)


@app.route("/api/zones")
@login_required
def api_zones():
    return jsonify(get_zones())


@app.route("/api/deploy", methods=["POST"])
@staff_required
def api_deploy():
    """Turns a Rescue Agent recommendation into a real state change: marks
    the nearest available team 'deployed' and reserves hospital beds.
    Previously the recommendation was purely advisory and never affected
    rescue_teams.status or hospitals.beds_available."""
    body = request.get_json(silent=True) or {}
    zone = body.get("zone")
    if not zone:
        return jsonify({"ok": False, "error": "zone is required"}), 400

    deployment = rescue_agent.deploy(zone, deployed_by=session.get("user_name"))
    if not deployment:
        return jsonify({"ok": False, "error": "No available team for that zone right now."}), 409

    log_audit(
        session.get("user_id"),
        session.get("user_name"),
        session.get("role"),
        "DEPLOY_TEAM",
        "deployment",
        str(deployment["id"]),
        f"Dispatched Team #{deployment['team_id']} to {zone} (Beds held: {deployment['beds_reserved']})",
        request.remote_addr,
    )

    log_alert(
        title="Team dispatched",
        message=f"Team #{deployment['team_id']} dispatched to {zone} by {session.get('user_name')}.",
        severity="Medium",
        zone=zone,
    )
    return jsonify({"ok": True, "deployment": deployment})


@app.route("/api/recall", methods=["POST"])
@staff_required
def api_recall():
    body = request.get_json(silent=True) or {}
    deployment_id = body.get("deployment_id")
    if not deployment_id:
        return jsonify({"ok": False, "error": "deployment_id is required"}), 400

    deployment = rescue_agent.recall(deployment_id)
    if not deployment:
        return jsonify({"ok": False, "error": "Deployment not found or already recalled."}), 404

    log_audit(
        session.get("user_id"),
        session.get("user_name"),
        session.get("role"),
        "RECALL_TEAM",
        "deployment",
        str(deployment_id),
        f"Recalled deployment #{deployment_id} (Team #{deployment['team_id']})",
        request.remote_addr,
    )

    return jsonify({"ok": True, "deployment": dict(deployment)})


# ---------------------------------------------------- Unified Map & Routing ----

@app.route("/api/map/data")
@login_required
def api_map_data():
    """Consolidated endpoint delivering all layers for the unified map:
    disaster zones, hospitals, rescue teams, active alert beacons, and
    auto-assigned hospital/rescue route polylines."""
    weather_assessments = weather_agent.assess_all_zones()
    weather_by_zone = {w["zone"]: w for w in weather_assessments}
    zones_list = get_zones()
    zone_coords = {z["name"]: (z["lat"], z["lon"]) for z in zones_list}

    # All hospitals with accessibility status
    hospitals = hospital_agent.get_all_hospitals_with_status(weather_by_zone)

    # All rescue teams with status
    teams = rescue_agent.get_all_teams()

    # Active alerts with lat/lon coordinates
    alerts_rows = query("SELECT * FROM alerts ORDER BY id DESC LIMIT 20")
    active_alerts = []
    for a in alerts_rows:
        ad = dict(a)
        if ad.get("zone") in zone_coords:
            ad["lat"], ad["lon"] = zone_coords[ad["zone"]]
            active_alerts.append(ad)

    # Multi-zone auto-assignments with routes
    assignments = auto_assignment_engine.compute_all_zone_assignments(weather_by_zone)

    return jsonify({
        "zones": weather_assessments,
        "hospitals": hospitals,
        "rescue_teams": teams,
        "active_alerts": active_alerts,
        "assignments": assignments,
        "iot_sensors": iot_agent.get_all_sensors_with_status(),
        "satellite": satellite_agent.get_satellite_overview(zones_list),
        "social_distress": social_media_agent.get_feed(limit=15),
    })


# --------------------------------------------- Real-Time Notifications API ----

@app.route("/api/notifications")
@staff_required
def api_notifications():
    """Recent notification transmissions across Email, SMS, and Push."""
    return jsonify({
        "history": get_recent_notifications(60),
        "live_events": get_recent_live_events(20),
    })


@app.route("/api/notifications/stream")
@login_required
def api_notifications_stream():
    """Server-Sent Events (SSE) push stream: pushes critical alerts, hazard updates,
    and emergency broadcasts to connected browsers in real-time."""
    def event_stream():
        q = subscribe()
        try:
            # Yield initial connection confirmation
            init_payload = json.dumps({"connected": True, "timestamp": time.time()})
            yield f"event: ping\ndata: {init_payload}\n\n"

            while True:
                try:
                    event = q.get(timeout=20)
                    yield f"event: {event['type']}\ndata: {json.dumps(event['data'])}\n\n"
                except Exception:
                    # Timeout keepalive
                    yield f": keepalive\n\n"
        finally:
            unsubscribe(q)

    return Response(
        event_stream(),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@app.route("/api/notifications/broadcast", methods=["POST"])
@staff_required
def api_notifications_broadcast():
    """Staff-triggered emergency broadcast across SMS, Email, and in-app Push."""
    body = request.get_json(silent=True) or {}
    title = (body.get("title") or "").strip()
    message = (body.get("message") or "").strip()
    zone = body.get("zone") or None
    channels = body.get("channels") or ["push", "email", "sms"]

    if not title or not message:
        return jsonify({"ok": False, "error": "Title and message are required."}), 400

    sender = session.get("user_name") or "Emergency Command"
    result = notification_service.dispatch_broadcast(
        title=title,
        message=message,
        zone=zone,
        channels=channels,
        sender=sender,
    )
    log_alert(
        title=f"BROADCAST: {title}",
        message=message,
        severity="High",
        zone=zone,
    )
    log_audit(
        session.get("user_id"),
        session.get("user_name"),
        session.get("role"),
        "BROADCAST_EMERGENCY",
        "broadcast",
        zone or "ALL_ZONES",
        f"Broadcast: '{title}' via {', '.join(channels)}",
        request.remote_addr,
    )
    return jsonify(result)


# --------------------------------------------- Hospital & Rescue Auto-Assign ----

@app.route("/api/auto-assign")
@staff_required
def api_auto_assign():
    """Returns auto-routed hospitals and nearest rescue assignments for all zones."""
    weather_assessments = weather_agent.assess_all_zones()
    weather_by_zone = {w["zone"]: w for w in weather_assessments}
    assignments = auto_assignment_engine.compute_all_zone_assignments(weather_by_zone)
    return jsonify({
        "ok": True,
        "assignments": assignments,
        "auto_dispatch_active": is_auto_dispatch_enabled(),
    })


@app.route("/api/auto-assign/execute", methods=["POST"])
@staff_required
def api_auto_assign_execute():
    """Executes auto-assignment for a specific zone or batch auto-assigns all."""
    body = request.get_json(silent=True) or {}
    zone = body.get("zone")
    execute_all = body.get("all", False)
    user_name = session.get("user_name") or "Command Operator"

    if execute_all:
        res = auto_assignment_engine.execute_all_assignments(user_name=user_name)
        return jsonify({"ok": True, **res})

    if not zone:
        return jsonify({"ok": False, "error": "zone parameter or all=true required."}), 400

    deployment = auto_assignment_engine.execute_assignment(zone, user_name=user_name)
    if not deployment:
        return jsonify({"ok": False, "error": "No available team or hospital for this zone."}), 409

    return jsonify({"ok": True, "deployment": dict(deployment)})


@app.route("/api/auto-assign/toggle", methods=["GET", "POST"])
@staff_required
def api_auto_assign_toggle():
    """Get or toggle autonomous dispatch mode (ON: auto-dispatches units when risk turns High)."""
    if request.method == "POST":
        body = request.get_json(silent=True) or {}
        enabled = bool(body.get("enabled", not is_auto_dispatch_enabled()))
        set_auto_dispatch_enabled(enabled)
        return jsonify({"ok": True, "auto_dispatch_active": enabled})

    return jsonify({"ok": True, "auto_dispatch_active": is_auto_dispatch_enabled()})


# ----------------------------------------------- Live Satellite Imagery API ----

@app.route("/api/satellite/live")
def api_satellite_live():
    """Live Doppler radar imagery & cloud reflectivity overview."""
    zones_list = get_zones()
    return jsonify(satellite_agent.get_satellite_overview(zones_list))


# --------------------------------------------- IoT Environmental Sensors API ----

@app.route("/api/iot/sensors")
def api_iot_sensors():
    """Active environmental IoT sensors with current flood/river depths and thresholds."""
    return jsonify({
        "ok": True,
        "sensors": iot_agent.get_all_sensors_with_status(),
    })


@app.route("/api/iot/telemetry", methods=["POST"])
def api_iot_telemetry():
    """Telemetry ingestion endpoint for physical IoT microcontrollers (ESP32/Arduino/MQTT)."""
    data = request.get_json(silent=True) or {}
    sensor_id = data.get("sensor_id")
    reading = data.get("reading")
    battery_pct = data.get("battery_pct")

    if not sensor_id or reading is None:
        return jsonify({"ok": False, "error": "sensor_id and reading are required"}), 400

    try:
        res = iot_agent.ingest_telemetry(sensor_id, float(reading), battery_pct)
        if not res:
            return jsonify({"ok": False, "error": f"Sensor '{sensor_id}' not found"}), 404
        return jsonify({"ok": True, "data": res})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


# ------------------------------------------ Social Media SOS Intelligence API ----

@app.route("/api/social/distress", methods=["GET", "POST"])
def api_social_distress():
    """Crowdsourced social media SOS distress intelligence stream strictly for Twitter, Instagram, and Facebook."""
    if request.method == "POST":
        data = request.get_json(silent=True) or {}
        platform = data.get("platform", "Twitter")
        username = data.get("username", "Anonymous")
        message = data.get("message", "").strip()
        zone = data.get("zone", "General")
        verified = bool(data.get("verified", False))
        post_url = data.get("post_url")

        if not message:
            return jsonify({"ok": False, "error": "Message is required"}), 400

        res = social_media_agent.ingest_social_post(
            platform=platform,
            username=username,
            message=message,
            zone=zone,
            verified=verified,
            post_url=post_url,
        )
        return jsonify({"ok": True, "post": res})

    zone = request.args.get("zone")
    platform = request.args.get("platform")
    verified_only = request.args.get("verified") in ("1", "true", "True")
    return jsonify({
        "ok": True,
        "distress_feed": social_media_agent.get_feed(limit=30, zone=zone, platform=platform, verified_only=verified_only),
    })


@app.route("/api/social/sync", methods=["POST"])
def api_social_sync():
    """Pulls real live breaking emergency & disaster news from verified public feeds."""
    try:
        pulled = social_media_agent.pull_real_social_data(limit=15)
        return jsonify({
            "ok": True,
            "pulled_count": len(pulled),
            "feed": social_media_agent.get_feed(limit=30),
        })
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


# ------------------------------------------------ Citizen Incident Reporting ----

@app.route("/citizen/report", methods=["GET", "POST"])
def citizen_report():
    """Public page for citizens to report disaster incidents (floods, landslides, blocked roads, fires)."""
    if request.method == "POST":
        name = request.form.get("reporter_name", "").strip()
        phone = request.form.get("reporter_phone", "").strip()
        disaster_type = request.form.get("disaster_type", "Flooding").strip()
        location = request.form.get("location", "").strip()
        description = request.form.get("description", "").strip()
        severity = request.form.get("severity", "High").strip()

        lat_raw = request.form.get("lat")
        lon_raw = request.form.get("lon")
        lat = float(lat_raw) if lat_raw else None
        lon = float(lon_raw) if lon_raw else None

        if not name or not phone or not description or not location:
            flash("Please fill in all required fields (Name, Phone, Location, and Description).", "error")
            return redirect(url_for("citizen_report"))

        create_emergency_report(name, phone, disaster_type, location, lat, lon, description, severity)
        flash("Emergency report submitted successfully. Response teams and duty officers have been notified.", "success")
        return redirect(url_for("citizen_report"))

    zones = get_zones()
    return render_template("citizen_report.html", zones=zones)


@app.route("/api/emergency/reports", methods=["GET"])
@staff_required
def api_emergency_reports():
    """Emergency incident reports queue for command dispatchers."""
    status = request.args.get("status")
    return jsonify({
        "ok": True,
        "reports": get_emergency_reports(limit=50, status=status),
    })


@app.route("/api/emergency/reports/<int:report_id>/status", methods=["POST"])
@staff_required
def api_emergency_report_update(report_id):
    """Updates status of an emergency report (Verified, Dispatched, Resolved)."""
    data = request.get_json(silent=True) or {}
    new_status = data.get("status", "Verified")
    update_emergency_report_status(report_id, new_status)
    return jsonify({"ok": True, "report_id": report_id, "status": new_status})


if __name__ == "__main__":
    init_db(seed=True)
    app.run(debug=Config.DEBUG, host="0.0.0.0", port=5000)
