"""
Lightweight SQLite data layer.

Uses Python's built-in sqlite3 module so the project runs with zero extra
dependencies. Swap `get_connection()` for a MySQL/PostgreSQL driver later
without touching the rest of the app, since every other module only calls
the functions in this file.

Changelog (hardening pass):
  - Added `zones` table. Zones used to live only in Config.ZONES, which meant
    adding a district required a code change + redeploy. They're now seeded
    from Config.DEFAULT_ZONES on first run but manageable afterwards from
    /admin/zones.
  - Added `zone_state` table. This replaces the in-memory
    `_last_alerted_level` dict that used to live in weather_agent.py — that
    dict reset on every restart and would silently diverge across worker
    processes under gunicorn -w N. State now lives in the DB, so it's shared
    and durable.
  - Added `login_attempts` table for basic brute-force rate limiting on
    /login (previously unlimited attempts against a known demo password).
  - Added `deployments` table so "recommend a team" can become "actually
    deploy a team" with a real, reversible state change (rescue_teams.status,
    hospitals.beds_available) instead of the same static suggestion forever.
  - `disasters` table is now actually written to (via log_disaster_event) —
    previously defined in the schema but nothing ever inserted into it.
  - Added citizen-facing accounts: `users.zone` (home zone, nullable) and
    `role='citizen'` as a valid, self-registerable role alongside the
    seeded admin/operator accounts. See app.py::signup.
"""

import sqlite3
import os
from datetime import datetime, timedelta
from werkzeug.security import generate_password_hash

from config import Config


def get_connection():
    conn = sqlite3.connect(Config.DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(seed=True):
    """Create tables if they don't exist and optionally seed demo data."""
    os.makedirs(os.path.dirname(Config.DATABASE_PATH), exist_ok=True)
    conn = get_connection()
    cur = conn.cursor()

    cur.executescript(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            email TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            role TEXT DEFAULT 'operator',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS zones (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL,
            lat REAL NOT NULL,
            lon REAL NOT NULL,
            density REAL NOT NULL DEFAULT 0.5,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS zone_state (
            zone TEXT PRIMARY KEY,
            last_risk_level TEXT,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS disasters (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            location TEXT NOT NULL,
            disaster_type TEXT NOT NULL,
            severity TEXT NOT NULL,
            probability REAL NOT NULL,
            prediction TEXT,
            lat REAL,
            lon REAL,
            date TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS hospitals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            hospital_name TEXT NOT NULL,
            location TEXT NOT NULL,
            lat REAL,
            lon REAL,
            beds_total INTEGER,
            beds_available INTEGER,
            doctors_available INTEGER,
            ambulances_available INTEGER,
            icu_available INTEGER,
            contact TEXT
        );

        CREATE TABLE IF NOT EXISTS traffic (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            location TEXT NOT NULL,
            lat REAL,
            lon REAL,
            congestion_level TEXT,
            road_status TEXT,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS rescue_teams (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            team_name TEXT NOT NULL,
            zone TEXT,
            lat REAL,
            lon REAL,
            vehicles INTEGER,
            ambulances INTEGER,
            fire_units INTEGER,
            personnel INTEGER,
            status TEXT DEFAULT 'available'
        );

        CREATE TABLE IF NOT EXISTS deployments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            team_id INTEGER NOT NULL,
            zone TEXT NOT NULL,
            hospital_id INTEGER,
            beds_reserved INTEGER DEFAULT 0,
            deployed_by TEXT,
            status TEXT DEFAULT 'active',
            deployed_at TEXT DEFAULT CURRENT_TIMESTAMP,
            recalled_at TEXT
        );

        CREATE TABLE IF NOT EXISTS alerts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            message TEXT NOT NULL,
            severity TEXT NOT NULL,
            zone TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS login_attempts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ip_address TEXT NOT NULL,
            email TEXT,
            success INTEGER NOT NULL,
            attempted_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        """
    )
    conn.commit()

    # Lightweight migrations for DBs created before these columns existed.
    # CREATE TABLE IF NOT EXISTS won't add columns to an already-existing
    # table, so this covers upgrading an existing database file in place.
    for statement in (
        "ALTER TABLE users ADD COLUMN zone TEXT",
        "ALTER TABLE users ADD COLUMN email_verified INTEGER DEFAULT 0",
        "ALTER TABLE users ADD COLUMN verification_code_hash TEXT",
        "ALTER TABLE users ADD COLUMN verification_expires TEXT",
        "ALTER TABLE users ADD COLUMN last_seen TEXT",
    ):
        try:
            cur.execute(statement)
            conn.commit()
        except sqlite3.OperationalError:
            pass  # column already exists

    if seed:
        _seed_demo_data(conn)

    conn.close()


def _seed_demo_data(conn):
    cur = conn.cursor()

    cur.execute("SELECT COUNT(*) AS c FROM users")
    if cur.fetchone()["c"] == 0:
        cur.execute(
            "INSERT INTO users (name, email, password_hash, role, email_verified) VALUES (?, ?, ?, ?, 1)",
            ("Duty Officer", "admin@disaster-response.local",
             generate_password_hash("admin123"), "admin"),
        )
        cur.execute(
            "INSERT INTO users (name, email, password_hash, role, email_verified) VALUES (?, ?, ?, ?, 1)",
            ("Field Operator", "operator@disaster-response.local",
             generate_password_hash("operator123"), "operator"),
        )
        cur.execute(
            "INSERT INTO users (name, email, password_hash, role, zone, email_verified) VALUES (?, ?, ?, ?, ?, 1)",
            ("Resident", "resident@disaster-response.local",
             generate_password_hash("resident123"), "citizen", "HSR Layout"),
        )

    cur.execute("SELECT COUNT(*) AS c FROM zones")
    if cur.fetchone()["c"] == 0:
        cur.executemany(
            "INSERT INTO zones (name, lat, lon, density) VALUES (?, ?, ?, ?)",
            [(z["name"], z["lat"], z["lon"], z["density"]) for z in Config.DEFAULT_ZONES],
        )

    cur.execute("SELECT COUNT(*) AS c FROM hospitals")
    if cur.fetchone()["c"] == 0:
        hospitals = [
    ("Columbia Asia Hospital, Sarjapur Road", "HSR Layout", 12.9101, 77.6520, 180, 40, 28, 5, 8, "080-6165-6262"),
    ("Motherhood Hospital, HSR Layout", "HSR Layout", 12.9110, 77.6440, 90, 20, 15, 3, 4, "080-4718-1000"),
    ("St. John's Medical College Hospital", "Koramangala", 12.9280, 77.6230, 250, 55, 40, 6, 12, "080-2206-5000"),
    ("Sakra World Hospital", "Bellandur", 12.9260, 77.6790, 200, 45, 32, 5, 10, "080-4969-4969"),
    ("Narayana Health City", "Electronic City", 12.8340, 77.6800, 300, 70, 45, 8, 15, "080-7122-2222"),
]
        cur.executemany(
            """INSERT INTO hospitals
               (hospital_name, location, lat, lon, beds_total, beds_available,
                doctors_available, ambulances_available, icu_available, contact)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            hospitals,
        )

    cur.execute("SELECT COUNT(*) AS c FROM rescue_teams")
    if cur.fetchone()["c"] == 0:
        teams = [
    ("HSR Layout Response Unit", "HSR Layout", 12.9080, 77.6460, 5, 3, 2, 24, "available"),
    ("Koramangala Response Unit", "Koramangala", 12.9340, 77.6230, 4, 2, 2, 18, "available"),
    ("Bellandur Response Unit", "Bellandur", 12.9290, 77.6770, 3, 2, 1, 15, "available"),
    ("BTM Layout Response Unit", "BTM Layout", 12.9150, 77.6090, 3, 1, 1, 12, "available"),
    ("Electronic City Response Unit", "Electronic City", 12.8440, 77.6590, 4, 2, 3, 20, "available"),
]
        cur.executemany(
            """INSERT INTO rescue_teams
               (team_name, zone, lat, lon, vehicles, ambulances, fire_units, personnel, status)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            teams,
        )

    conn.commit()


def dict_from_row(row):
    return dict(row) if row else None


def query(sql, params=(), fetchone=False):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(sql, params)
    if sql.strip().upper().startswith("SELECT"):
        rows = cur.fetchone() if fetchone else cur.fetchall()
        conn.close()
        if fetchone:
            return dict_from_row(rows)
        return [dict(r) for r in rows]
    conn.commit()
    last_id = cur.lastrowid
    conn.close()
    return last_id


def log_alert(title, message, severity, zone=None):
    return query(
        "INSERT INTO alerts (title, message, severity, zone) VALUES (?, ?, ?, ?)",
        (title, message, severity, zone),
    )


# --------------------------------------------------------------- zones -----

def get_zones():
    """Zones now live in the DB (seeded from Config.DEFAULT_ZONES) so they
    can be managed at runtime from /admin/zones instead of requiring a code
    change + redeploy for every new district."""
    rows = query("SELECT * FROM zones ORDER BY name")
    if not rows:
        # DB not seeded yet (e.g. tests importing agents directly) — fall
        # back to the static defaults rather than returning an empty list.
        return list(Config.DEFAULT_ZONES)
    return rows


def add_zone(name, lat, lon, density):
    return query(
        "INSERT INTO zones (name, lat, lon, density) VALUES (?, ?, ?, ?)",
        (name, lat, lon, density),
    )


def delete_zone(zone_id):
    query("DELETE FROM zones WHERE id = ?", (zone_id,))


# ---------------------------------------------------------- zone state -----

def get_zone_state(zone_name):
    row = query("SELECT * FROM zone_state WHERE zone = ?", (zone_name,), fetchone=True)
    return row["last_risk_level"] if row else None


def set_zone_state(zone_name, risk_level):
    query(
        """INSERT INTO zone_state (zone, last_risk_level, updated_at)
           VALUES (?, ?, CURRENT_TIMESTAMP)
           ON CONFLICT(zone) DO UPDATE SET
             last_risk_level = excluded.last_risk_level,
             updated_at = CURRENT_TIMESTAMP""",
        (zone_name, risk_level),
    )


# ------------------------------------------------------------ disasters ----

def log_disaster_event(location, disaster_type, severity, probability, prediction, lat, lon):
    """Writes a row to the `disasters` history table. The schema existed
    from the start but nothing ever inserted into it — every assessment
    was ephemeral. Called once per zone assessment so /admin and
    /api/incidents have real history to show, not just the live snapshot."""
    return query(
        """INSERT INTO disasters
           (location, disaster_type, severity, probability, prediction, lat, lon)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (location, disaster_type, severity, probability, prediction, lat, lon),
    )


def get_recent_disasters(limit=50):
    return query("SELECT * FROM disasters ORDER BY id DESC LIMIT ?", (limit,))


# ------------------------------------------------------------ deployment ---

def create_deployment(team_id, zone, hospital_id=None, beds_reserved=0, deployed_by=None):
    return query(
        """INSERT INTO deployments (team_id, zone, hospital_id, beds_reserved, deployed_by)
           VALUES (?, ?, ?, ?, ?)""",
        (team_id, zone, hospital_id, beds_reserved, deployed_by),
    )


def get_active_deployments():
    return query("SELECT * FROM deployments WHERE status = 'active' ORDER BY deployed_at DESC")


def get_deployment(deployment_id):
    return query("SELECT * FROM deployments WHERE id = ?", (deployment_id,), fetchone=True)


def close_deployment(deployment_id):
    query(
        "UPDATE deployments SET status = 'recalled', recalled_at = CURRENT_TIMESTAMP WHERE id = ?",
        (deployment_id,),
    )


def set_team_status(team_id, status):
    query("UPDATE rescue_teams SET status = ? WHERE id = ?", (status, team_id))


def adjust_hospital_beds(hospital_id, delta):
    """delta can be negative (reserve) or positive (release). Clamped to
    [0, beds_total] so it can never go negative or exceed capacity."""
    hosp = query("SELECT beds_total, beds_available FROM hospitals WHERE id = ?",
                 (hospital_id,), fetchone=True)
    if not hosp:
        return
    new_val = max(0, min(hosp["beds_total"], hosp["beds_available"] + delta))
    query("UPDATE hospitals SET beds_available = ? WHERE id = ?", (new_val, hospital_id))


# ------------------------------------------------------- login rate limit --

def record_login_attempt(ip_address, email, success):
    query(
        "INSERT INTO login_attempts (ip_address, email, success) VALUES (?, ?, ?)",
        (ip_address, email, 1 if success else 0),
    )


def count_recent_failed_attempts(ip_address, minutes=15):
    cutoff = (datetime.utcnow() - timedelta(minutes=minutes)).strftime("%Y-%m-%d %H:%M:%S")
    row = query(
        """SELECT COUNT(*) AS c FROM login_attempts
           WHERE ip_address = ? AND success = 0 AND attempted_at >= ?""",
        (ip_address, cutoff),
        fetchone=True,
    )
    return row["c"] if row else 0


# ------------------------------------------------------------ citizen ------

def get_user_by_email(email):
    return query("SELECT * FROM users WHERE email = ?", (email,), fetchone=True)


def create_citizen(name, email, password_hash, zone=None):
    """Public signup always creates role='citizen' — there is no way to
    self-register as admin/operator through this function; those accounts
    are only ever created by seeding or directly in the database. Starts
    unverified; see set_verification_code()/mark_email_verified()."""
    return query(
        "INSERT INTO users (name, email, password_hash, role, zone, email_verified) "
        "VALUES (?, ?, ?, 'citizen', ?, 0)",
        (name, email, password_hash, zone),
    )


def get_user_by_id(user_id):
    return query("SELECT * FROM users WHERE id = ?", (user_id,), fetchone=True)


# ------------------------------------------------- email verification -----

def set_verification_code(user_id, code_hash, expires_at):
    query(
        "UPDATE users SET verification_code_hash = ?, verification_expires = ? WHERE id = ?",
        (code_hash, expires_at, user_id),
    )


def check_verification_code(user_id, code_hash):
    """Returns True only if the hash matches AND the code hasn't expired.
    Does not consume the code — call mark_email_verified() afterward."""
    user = get_user_by_id(user_id)
    if not user or not user["verification_code_hash"]:
        return False
    if user["verification_code_hash"] != code_hash:
        return False
    if not user["verification_expires"]:
        return False
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S") <= user["verification_expires"]


def mark_email_verified(user_id):
    query(
        "UPDATE users SET email_verified = 1, verification_code_hash = NULL, "
        "verification_expires = NULL WHERE id = ?",
        (user_id,),
    )


# ------------------------------------------------------- active users -----

def touch_last_seen(user_id):
    query(
        "UPDATE users SET last_seen = CURRENT_TIMESTAMP WHERE id = ?",
        (user_id,),
    )


def count_active_citizens(minutes=5):
    """"Active" = a citizen account whose last_seen falls within the
    window — updated on every authenticated request via
    app.py::touch_activity, so this reflects genuinely open sessions, not
    just "logged in at some point"."""
    cutoff = (datetime.utcnow() - timedelta(minutes=minutes)).strftime("%Y-%m-%d %H:%M:%S")
    row = query(
        "SELECT COUNT(*) AS c FROM users WHERE role = 'citizen' AND last_seen >= ?",
        (cutoff,),
        fetchone=True,
    )
    return row["c"] if row else 0


def citizen_account_stats():
    total = query("SELECT COUNT(*) AS c FROM users WHERE role = 'citizen'", fetchone=True)["c"]
    verified = query(
        "SELECT COUNT(*) AS c FROM users WHERE role = 'citizen' AND email_verified = 1",
        fetchone=True,
    )["c"]
    return {
        "total_citizens": total,
        "verified_citizens": verified,
        "active_now": count_active_citizens(),
    }


def get_recent_citizens(limit=25):
    return query(
        "SELECT id, name, email, zone, email_verified, last_seen, created_at "
        "FROM users WHERE role = 'citizen' ORDER BY created_at DESC LIMIT ?",
        (limit,),
    )
