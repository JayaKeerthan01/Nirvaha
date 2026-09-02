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
Demo login (operator, no zone-management access):
    email:    operator@disaster-response.local
    password: operator123
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
from functools import wraps

from flask import (
    Flask, render_template, redirect, url_for, session, request,
    jsonify, flash, abort,
)
from werkzeug.security import check_password_hash, generate_password_hash

from config import Config, DEFAULT_SECRET_KEY
from database.db import (
    init_db, query, get_zones, add_zone, delete_zone,
    get_recent_disasters, record_login_attempt, count_recent_failed_attempts,
    log_alert, get_user_by_email, create_citizen,
)
from agents.weather_agent import weather_agent
from agents.traffic_agent import traffic_agent
from agents.hospital_agent import hospital_agent
from agents.rescue_agent import rescue_agent
from agents.coordinator_agent import coordinator_agent
from agents.citizen_chat_agent import answer as chat_answer

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
    submitted = request.form.get("csrf_token") or request.headers.get("X-CSRFToken")
    if not submitted and request.is_json:
        submitted = (request.get_json(silent=True) or {}).get("csrf_token")
    expected = session.get("csrf_token", "")
    if not submitted or not expected or not secrets.compare_digest(str(submitted), str(expected)):
        abort(400, description="Missing or invalid CSRF token. Reload the page and try again.")


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
        user = query("SELECT * FROM users WHERE email = ?", (email,), fetchone=True)
        success = bool(user and check_password_hash(user["password_hash"], password))
        record_login_attempt(ip, email, success)

        if success:
            session["user_id"] = user["id"]
            session["user_name"] = user["name"]
            session["role"] = user["role"]
            session["zone"] = user["zone"]
            default_next = url_for("citizen_home") if user["role"] == "citizen" else url_for("dashboard")
            return redirect(request.args.get("next") or default_next)
        flash("Invalid email or password.", "error")
    return render_template("login.html")


@app.route("/signup", methods=["GET", "POST"])
def signup():
    """Public self-service signup — always creates a 'citizen' role account.
    There is no path from this form to admin/operator; those are only ever
    created by seeding or directly in the database."""
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        zone = request.form.get("zone") or None

        errors = []
        if not name:
            errors.append("Name is required.")
        if not email or "@" not in email:
            errors.append("A valid email is required.")
        if len(password) < 8:
            errors.append("Password must be at least 8 characters.")
        if not errors and get_user_by_email(email):
            errors.append("An account with that email already exists.")

        if errors:
            for e in errors:
                flash(e, "error")
            return render_template("signup.html", zones=get_zones(), name=name, email=email, zone=zone)

        user_id = create_citizen(name, email, generate_password_hash(password), zone)
        session["user_id"] = user_id
        session["user_name"] = name
        session["role"] = "citizen"
        session["zone"] = zone
        flash(f"Welcome, {name}. Your account is ready.", "success")
        return redirect(url_for("citizen_home"))

    return render_template("signup.html", zones=get_zones())


@app.route("/logout")
def logout():
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
            flash(f"Zone '{name}' added.", "success")
        except Exception as exc:
            flash(f"Could not add zone: {exc}", "error")
        return redirect(url_for("admin_zones"))

    return render_template("admin_zones.html", zones=get_zones())


@app.route("/admin/zones/<int:zone_id>/delete", methods=["POST"])
@admin_required
def admin_delete_zone(zone_id):
    delete_zone(zone_id)
    flash("Zone removed.", "success")
    return redirect(url_for("admin_zones"))


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
    return render_template("citizen_chat.html")


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
    return jsonify(hospital_agent.get_all_hospitals())


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
    route = traffic_agent.safest_route(from_zone_name, traffic_assessments)
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

    return jsonify({"ok": True, "deployment": dict(deployment)})


if __name__ == "__main__":
    init_db(seed=True)
    app.run(debug=Config.DEBUG, host="0.0.0.0", port=5000)
