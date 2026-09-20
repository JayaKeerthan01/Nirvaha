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
    seeded admin/sub-admin accounts. See app.py::signup.
"""

import sqlite3
import os
from datetime import datetime, timedelta
from werkzeug.security import generate_password_hash

from config import Config


def get_connection():
    conn = sqlite3.connect(Config.DATABASE_PATH, timeout=20.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    conn.execute("PRAGMA cache_size = -64000")
    conn.execute("PRAGMA temp_store = MEMORY")
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
            phone TEXT,
            zone TEXT,
            email_verified INTEGER DEFAULT 0,
            verification_code_hash TEXT,
            verification_expires TEXT,
            last_seen TEXT,
            whatsapp_subscribed INTEGER DEFAULT 1,
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

        CREATE TABLE IF NOT EXISTS notifications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            alert_id INTEGER,
            channel TEXT NOT NULL,
            recipient TEXT NOT NULL,
            title TEXT NOT NULL,
            message TEXT NOT NULL,
            zone TEXT,
            status TEXT DEFAULT 'sent',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS system_settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS audit_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            user_name TEXT,
            role TEXT,
            action TEXT NOT NULL,
            target_type TEXT,
            target_id TEXT,
            details TEXT,
            ip_address TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS password_resets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            reset_code_hash TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            used INTEGER DEFAULT 0,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS shelters (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            zone TEXT NOT NULL,
            lat REAL NOT NULL,
            lon REAL NOT NULL,
            capacity INTEGER DEFAULT 500,
            current_occupancy INTEGER DEFAULT 0,
            contact TEXT,
            status TEXT DEFAULT 'open'
        );

        CREATE TABLE IF NOT EXISTS iot_sensors (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sensor_id TEXT UNIQUE NOT NULL,
            sensor_name TEXT NOT NULL,
            sensor_type TEXT NOT NULL,
            zone TEXT NOT NULL,
            lat REAL NOT NULL,
            lon REAL NOT NULL,
            current_value REAL NOT NULL,
            unit TEXT NOT NULL,
            warning_threshold REAL NOT NULL,
            critical_threshold REAL NOT NULL,
            status TEXT DEFAULT 'Normal',
            battery_pct INTEGER DEFAULT 100,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS social_distress (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            platform TEXT NOT NULL,
            username TEXT NOT NULL,
            message TEXT NOT NULL,
            zone TEXT NOT NULL,
            urgency_level TEXT DEFAULT 'High',
            sentiment REAL DEFAULT -0.8,
            verified INTEGER DEFAULT 0,
            post_url TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS emergency_reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            reporter_name TEXT NOT NULL,
            reporter_phone TEXT NOT NULL,
            disaster_type TEXT NOT NULL,
            location TEXT NOT NULL,
            lat REAL,
            lon REAL,
            description TEXT NOT NULL,
            severity TEXT DEFAULT 'High',
            status TEXT DEFAULT 'Pending',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS relief_supplies (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            shelter_id INTEGER NOT NULL,
            item_name TEXT NOT NULL,
            quantity INTEGER NOT NULL DEFAULT 0,
            unit TEXT NOT NULL DEFAULT 'units',
            minimum_required INTEGER NOT NULL DEFAULT 100,
            status TEXT DEFAULT 'Adequate',
            last_updated TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (shelter_id) REFERENCES shelters(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS relief_volunteers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ngo_name TEXT NOT NULL,
            contact_person TEXT NOT NULL,
            phone TEXT NOT NULL,
            assigned_zone TEXT NOT NULL,
            active_volunteers INTEGER NOT NULL DEFAULT 10,
            specialization TEXT DEFAULT 'General Relief',
            status TEXT DEFAULT 'Active',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS press_releases (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            bulletin_no TEXT UNIQUE NOT NULL,
            headline TEXT NOT NULL,
            zone TEXT NOT NULL,
            disaster_type TEXT NOT NULL,
            official_statement TEXT NOT NULL,
            verified_casualties INTEGER DEFAULT 0,
            evacuees_count INTEGER DEFAULT 0,
            sheltered_count INTEGER DEFAULT 0,
            spokesperson TEXT DEFAULT 'State Disaster Management Authority',
            media_contact TEXT DEFAULT 'press@nirvaha.gov.in / 080-2200-9999',
            status TEXT DEFAULT 'Published',
            published_at TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS whatsapp_subscribers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            name TEXT NOT NULL,
            phone TEXT UNIQUE NOT NULL,
            zone TEXT,
            channel_name TEXT DEFAULT 'Nirvaha Official Emergency Broadcast',
            status TEXT DEFAULT 'Subscribed',
            enrolled_at TEXT DEFAULT CURRENT_TIMESTAMP,
            last_alert_sent TEXT,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE SET NULL
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
        "ALTER TABLE users ADD COLUMN phone TEXT",
        "ALTER TABLE users ADD COLUMN whatsapp_subscribed INTEGER DEFAULT 1",
    ):
        try:
            cur.execute(statement)
            conn.commit()
        except sqlite3.OperationalError:
            pass  # column already exists

    # Safely create all indexes after table definitions & column migrations
    for idx_stmt in (
        "CREATE INDEX IF NOT EXISTS idx_users_email ON users(email)",
        "CREATE INDEX IF NOT EXISTS idx_users_zone ON users(zone)",
        "CREATE INDEX IF NOT EXISTS idx_alerts_zone ON alerts(zone)",
        "CREATE INDEX IF NOT EXISTS idx_alerts_created ON alerts(created_at)",
        "CREATE INDEX IF NOT EXISTS idx_disasters_loc_date ON disasters(location, date)",
        "CREATE INDEX IF NOT EXISTS idx_deployments_status ON deployments(status)",
        "CREATE INDEX IF NOT EXISTS idx_audit_created ON audit_logs(created_at)",
        "CREATE INDEX IF NOT EXISTS idx_notifications_created ON notifications(created_at)",
        "CREATE INDEX IF NOT EXISTS idx_iot_sensors_zone ON iot_sensors(zone)",
        "CREATE INDEX IF NOT EXISTS idx_social_distress_zone ON social_distress(zone)",
        "CREATE INDEX IF NOT EXISTS idx_emergency_reports_status ON emergency_reports(status)",
        "CREATE INDEX IF NOT EXISTS idx_relief_supplies_shelter ON relief_supplies(shelter_id)",
        "CREATE INDEX IF NOT EXISTS idx_press_releases_published ON press_releases(published_at)",
        "CREATE INDEX IF NOT EXISTS idx_whatsapp_subscribers_phone ON whatsapp_subscribers(phone)",
        "CREATE INDEX IF NOT EXISTS idx_whatsapp_subscribers_zone ON whatsapp_subscribers(zone)",
    ):
        try:
            cur.execute(idx_stmt)
            conn.commit()
        except Exception:
            pass

    # Deduplicate hospitals and enforce unique index
    try:
        cur.execute("""
            DELETE FROM hospitals WHERE id NOT IN (
                SELECT MIN(id) FROM hospitals GROUP BY LOWER(TRIM(hospital_name)), LOWER(TRIM(location))
            )
        """)
        cur.execute("DELETE FROM hospitals WHERE contact = 'Zone Service Desk' OR contact LIKE '%Desk%'")
        conn.commit()
    except Exception:
        pass

    try:
        cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_hospitals_name_loc ON hospitals(hospital_name, location)")
        conn.commit()
    except Exception:
        pass

    try:
        cur.execute("ALTER TABLE social_distress ADD COLUMN post_url TEXT")
        conn.commit()
    except Exception:
        pass

    try:
        # Enforce strictly Twitter, Instagram, and Facebook
        cur.execute("DELETE FROM social_distress WHERE platform NOT IN ('Twitter', 'Instagram', 'Facebook')")
        conn.commit()
    except Exception:
        pass

    if seed:
        _seed_demo_data(conn)

    conn.close()


def _seed_demo_data(conn):
    cur = conn.cursor()

    cur.execute("UPDATE users SET role = 'operator' WHERE role = 'sub_admin'")

    legacy_email = "operator@disaster-response.local"
    subadmin_email = "subadmin@disaster-response.local"
    row = cur.execute(
        "SELECT id FROM users WHERE email IN (?, ?)",
        (legacy_email, subadmin_email),
    ).fetchone()
    if row:
        cur.execute(
            "UPDATE users SET name = ?, email = ?, password_hash = ?, role = 'operator', email_verified = 1 WHERE email IN (?, ?)",
            ("Sub Admin", subadmin_email, generate_password_hash("subadmin123"), legacy_email, subadmin_email),
        )
    else:
        cur.execute(
            "INSERT INTO users (name, email, password_hash, role, email_verified) VALUES (?, ?, ?, ?, 1)",
            ("Sub Admin", subadmin_email, generate_password_hash("subadmin123"), "operator"),
        )

    cur.execute("SELECT COUNT(*) AS c FROM users WHERE email = ?", ("admin@disaster-response.local",))
    if cur.fetchone()["c"] == 0:
        cur.execute(
            "INSERT INTO users (name, email, password_hash, role, email_verified) VALUES (?, ?, ?, ?, 1)",
            ("Duty Officer", "admin@disaster-response.local",
             generate_password_hash("admin123"), "admin"),
        )

    cur.execute("SELECT COUNT(*) AS c FROM users WHERE email = ?", (subadmin_email,))
    if cur.fetchone()["c"] == 0:
        cur.execute(
            "INSERT INTO users (name, email, password_hash, role, email_verified) VALUES (?, ?, ?, ?, 1)",
            ("Sub Admin", subadmin_email, generate_password_hash("subadmin123"), "operator"),
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
    # HSR Layout
    ("Columbia Asia Hospital, Sarjapur Road", "HSR Layout", 12.9101, 77.6520, 180, 40, 28, 5, 8, "080-6165-6262"),
    ("Motherhood Hospital, HSR Layout", "HSR Layout", 12.9110, 77.6440, 90, 20, 15, 3, 4, "080-4718-1000"),
    ("Sagar Hospitals, HSR Layout", "HSR Layout", 12.9050, 77.6510, 150, 35, 22, 4, 6, "080-4243-4243"),
    # Koramangala
    ("St. John's Medical College Hospital", "Koramangala", 12.9280, 77.6230, 250, 55, 40, 6, 12, "080-2206-5000"),
    ("Manipal Hospital, Koramangala", "Koramangala", 12.9345, 77.6200, 280, 60, 45, 7, 14, "080-2502-4444"),
    ("Cloudnine Hospital, Koramangala", "Koramangala", 12.9380, 77.6270, 100, 25, 20, 3, 5, "080-3989-9999"),
    # Bellandur
    ("Sakra World Hospital", "Bellandur", 12.9260, 77.6790, 200, 45, 32, 5, 10, "080-4969-4969"),
    ("Vydehi Multispecialty, Bellandur", "Bellandur", 12.9330, 77.6820, 220, 50, 35, 6, 11, "080-2841-3333"),
    ("Aster CMI Extension, Bellandur", "Bellandur", 12.9270, 77.6750, 130, 28, 24, 4, 7, "080-4342-0100"),
    # BTM Layout
    ("Fortis Hospital, BTM Layout", "BTM Layout", 12.9140, 77.6080, 260, 55, 40, 6, 13, "080-6621-4444"),
    ("Sparsh Hospital, BTM Layout", "BTM Layout", 12.9190, 77.6130, 120, 30, 18, 3, 6, "080-4969-9797"),
    ("People Tree Hospital, BTM Layout", "BTM Layout", 12.9110, 77.6060, 90, 20, 15, 2, 4, "080-2668-5555"),
    # Electronic City
    ("Narayana Health City", "Electronic City", 12.8340, 77.6800, 300, 70, 45, 8, 15, "080-7122-2222"),
    ("BGS Gleneagles Global, Electronic City", "Electronic City", 12.8420, 77.6650, 200, 45, 32, 5, 10, "080-2504-2222"),
    ("Apollo Clinic, Electronic City", "Electronic City", 12.8480, 77.6550, 110, 24, 19, 3, 5, "080-4055-9999"),
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

    cur.execute("SELECT COUNT(*) AS c FROM shelters")
    if cur.fetchone()["c"] == 0:
        demo_shelters = [
            ("HSR Central Disaster Relief Shelter", "HSR Layout", 12.9130, 77.6410, 800, 120, "080-2500-1111"),
            ("Bellandur High-Ground Relief Center", "Bellandur", 12.9350, 77.6740, 600, 45, "080-2500-2222"),
            ("BTM Community Relief Complex", "BTM Layout", 12.9210, 77.6050, 750, 90, "080-2500-3333"),
            ("Electronic City Emergency Shelter", "Electronic City", 12.8510, 77.6620, 1000, 150, "080-2500-4444"),
            ("Koramangala Indoor Relief Camp", "Koramangala", 12.9390, 77.6210, 850, 80, "080-2500-5555"),
        ]
        cur.executemany(
            "INSERT INTO shelters (name, zone, lat, lon, capacity, current_occupancy, contact) VALUES (?, ?, ?, ?, ?, ?, ?)",
            demo_shelters,
        )

    # For any monitored zone that currently has fewer than 2 real hospitals, auto-populate real hospitals
    try:
        from utils.hospital_search import get_real_hospitals_for_location
        all_zones = cur.execute("SELECT name, lat, lon FROM zones").fetchall()
        for z in all_zones:
            z_name = z["name"]
            h_cnt = cur.execute("SELECT COUNT(*) AS c FROM hospitals WHERE LOWER(TRIM(location)) = LOWER(TRIM(?))", (z_name,)).fetchone()["c"]
            if h_cnt < 2:
                real_hosps = get_real_hospitals_for_location(z_name, z["lat"], z["lon"])
                for rh in real_hosps:
                    cur.execute(
                        """INSERT OR IGNORE INTO hospitals
                           (hospital_name, location, lat, lon, beds_total, beds_available,
                            doctors_available, ambulances_available, icu_available, contact)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (
                            rh["hospital_name"],
                            z_name,
                            rh["lat"],
                            rh["lon"],
                            rh["beds_total"],
                            rh["beds_available"],
                            rh["doctors_available"],
                            rh["ambulances_available"],
                            rh["icu_available"],
                            rh["contact"],
                        ),
                    )
    except Exception:
        pass

    # Seed initial IoT Sensors
    cur.execute("SELECT COUNT(*) AS c FROM iot_sensors")
    if cur.fetchone()["c"] == 0:
        demo_sensors = [
            ("SENSOR-HSR-AGARA-01", "Agara Lake Inflow Water Depth Gauge", "Water Level", "HSR Layout", 12.9190, 77.6430, 1.85, "m", 2.5, 3.2, "Normal", 94),
            ("SENSOR-BELL-OUTFLOW-02", "Bellandur Lake Spillway Crest Gauge", "Water Level", "Bellandur", 12.9350, 77.6740, 2.30, "m", 2.8, 3.5, "Normal", 89),
            ("SENSOR-BTM-MADIWALA-03", "Madiwala Lake Wetland Level Telemetry", "Water Level", "BTM Layout", 12.9180, 77.6180, 1.40, "m", 2.2, 3.0, "Normal", 96),
            ("SENSOR-ECITY-DRAIN-04", "Electronic City Phase 1 Main Storm Drain", "Flow Depth", "Electronic City", 12.8480, 77.6620, 0.75, "m", 1.8, 2.5, "Normal", 91),
            ("SENSOR-KORA-VALLEY-05", "Koramangala Valley Stormwater Culvert Sensor", "Water Level", "Koramangala", 12.9310, 77.6250, 1.65, "m", 2.4, 3.1, "Normal", 88),
        ]
        cur.executemany(
            """INSERT INTO iot_sensors (sensor_id, sensor_name, sensor_type, zone, lat, lon, current_value, unit, warning_threshold, critical_threshold, status, battery_pct)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            demo_sensors,
        )

    # Seed initial Social Media Distress Feed (Strictly Twitter, Instagram, and Facebook)
    cur.execute("SELECT COUNT(*) AS c FROM social_distress")
    if cur.fetchone()["c"] == 0:
        demo_social = [
            ("Twitter", "@KarnatakaSNDMC", "Heavy rainfall warning (Orange Alert) issued for Bengaluru Urban & South taluks for next 24 hours. Emergency response teams on standby. #BangaloreRains", "Bellandur", "High", -0.70, 1, "https://x.com/KarnatakaSNDMC"),
            ("Twitter", "@BlrCityPolice", "Traffic diversion in effect near 100ft Road Koramangala & Sony World junction due to water accumulation. Commuters requested to take alternate routes. Dial 112 for rescue.", "Koramangala", "High", -0.65, 1, "https://x.com/BlrCityPolice"),
            ("Twitter", "@BBMPCOMM", "BBMP Emergency Flood Control Rooms activated across all 8 zones. Citizens facing fallen trees or severe waterlogging can call 1533 or 080-22660000.", "HSR Layout", "High", -0.50, 1, "https://x.com/BBMPCOMM"),
            ("Twitter", "@TOIBengaluru", "Water entered several apartment basements along Outer Ring Road following sudden high-intensity downpour. BBMP pumping motors deployed.", "Bellandur", "Critical", -0.85, 1, "https://x.com/TOIBengaluru"),
            ("Instagram", "@bengalurucitypolice", "URGENT PUBLIC ADVISORY: Waterlogging reported near Silk Board junction and BTM 2nd Stage. Our officers are assisting stranded motorists. Avoid unnecessary travel.", "BTM Layout", "High", -0.65, 1, "https://www.instagram.com/bengalurucitypolice"),
            ("Instagram", "@karnatakastatepolice", "SDRF and Fire & Emergency Services deployed inflatable boats in low-lying residential clusters of Bellandur and HSR Layout for evacuation of senior citizens.", "Bellandur", "Critical", -0.90, 1, "https://www.instagram.com/karnatakastatepolice"),
            ("Instagram", "@bbmp.official", "Precautionary tree-trimming and stormwater desilting operations underway in Koramangala and Electronic City corridors. 24x7 control team active.", "Electronic City", "Moderate", -0.40, 1, "https://www.instagram.com/bbmp.official"),
            ("Instagram", "@bangaloretimesofficial", "Flash rain leaves multiple key roads inundated in Bengaluru South. Citizens share videos of submerged subways.", "BTM Layout", "Moderate", -0.55, 1, "https://www.instagram.com/bangaloretimesofficial"),
            ("Facebook", "Karnataka State Natural Disaster Monitoring Centre (KSNDMC)", "Rainfall Summary & Forecast: Widespread heavy rainfall recorded across Bengaluru. Madiwala Lake and Bellandur catchment levels rising. Citizens in low-lying valleys advised to remain vigilant.", "BTM Layout", "High", -0.75, 1, "https://www.facebook.com/KSNDMC"),
            ("Facebook", "Bengaluru City Police Emergency Response", "Control Room Dispatch Update: Emergency calls from Koramangala and HSR Layout dispatched to field rescue units. 4 quick response teams actively assisting residents.", "Koramangala", "High", -0.60, 1, "https://www.facebook.com/blrcitypolice"),
            ("Facebook", "BBMP Disaster Management Control Room", "Helpline Numbers for Urban Flooding: Central Control Room 080-22221188 / 9480685700. WhatsApp helpline active for sharing geo-tagged incident photos.", "HSR Layout", "Moderate", -0.30, 1, "https://www.facebook.com/bbmp.controlroom"),
            ("Facebook", "Bengaluru Traffic Police (BTP) Live Updates", "Heavy water stagnation under Electronic City elevated toll plaza and Silk Board junction. Traffic moving at slow pace. Recovery cranes stationed.", "Electronic City", "Moderate", -0.50, 1, "https://www.facebook.com/bangaloretrafficpolice"),
        ]
        cur.executemany(
            """INSERT INTO social_distress (platform, username, message, zone, urgency_level, sentiment, verified, post_url)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            demo_social,
        )

    # Seed initial Relief Supplies for shelters
    cur.execute("SELECT COUNT(*) AS c FROM relief_supplies")
    if cur.fetchone()["c"] == 0:
        shelter_rows = cur.execute("SELECT id, name, zone FROM shelters").fetchall()
        demo_supplies = []
        for s in shelter_rows:
            sid = s["id"]
            demo_supplies.extend([
                (sid, "Potable Drinking Water (5L Cans)", 450, "cans", 200, "Adequate"),
                (sid, "Ready-to-Eat Food Rations", 750, "packs", 300, "Adequate"),
                (sid, "Emergency Trauma & First Aid Kits", 60, "kits", 40, "Adequate"),
                (sid, "Thermal Blankets & Bed Rolls", 380, "units", 250, "Adequate"),
                (sid, "Water Purification Tablets", 1200, "strips", 500, "Adequate"),
                (sid, "Infant Formula & Baby Care Kits", 85, "kits", 50, "Adequate"),
            ])
        cur.executemany(
            """INSERT INTO relief_supplies (shelter_id, item_name, quantity, unit, minimum_required, status)
               VALUES (?, ?, ?, ?, ?, ?)""",
            demo_supplies,
        )

    # Seed initial NGO Relief Partners & Volunteers
    cur.execute("SELECT COUNT(*) AS c FROM relief_volunteers")
    if cur.fetchone()["c"] == 0:
        demo_volunteers = [
            ("Indian Red Cross Society (Karnataka)", "Dr. Arvind Rao", "+91-98800-REDX-01", "HSR Layout", 45, "Medical Aid & Triage", "Active"),
            ("Goonj Disaster Relief Initiative", "Meera Kulkarni", "+91-98800-GNJ-02", "Bellandur", 38, "Food & Clothing Distribution", "Active"),
            ("Rapid Response Disaster Relief NGO", "Kiran Hegde", "+91-98800-RRD-03", "Koramangala", 30, "Shelter Management & Water Purification", "Active"),
            ("Akshaya Patra Emergency Feeding Wing", "R. Swaminathan", "+91-98800-AKP-04", "BTM Layout", 55, "Hot Meals & Mass Kitchen", "Active"),
            ("SEEDS India Community Resilience Cell", "Pooja Deshmukh", "+91-98800-SDS-05", "Electronic City", 25, "Logistics & Search Support", "Active"),
        ]
        cur.executemany(
            """INSERT INTO relief_volunteers (ngo_name, contact_person, phone, assigned_zone, active_volunteers, specialization, status)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            demo_volunteers,
        )

    # Seed initial Media Press Releases & Verified Situation Reports
    cur.execute("SELECT COUNT(*) AS c FROM press_releases")
    if cur.fetchone()["c"] == 0:
        demo_press = [
            (
                "SITREP-2026-09-001",
                "Official Advisory: State Disaster Authority Issues Urban Flood Preparedness Guidance for Bengaluru South",
                "Bellandur",
                "Urban Flooding",
                "The State Disaster Management Authority (SDMA) has activated 5 regional emergency coordination hubs across Bengaluru. Moderate to heavy catchment rainfall has caused controlled spillway runoff at Bellandur and Madiwala lakes. Citizens are advised to follow official evacuation corridors. All 15 district hospitals maintain dedicated trauma bed reserves. Beware of unverified social media rumors.",
                0,
                380,
                245,
                "Director of Emergency Communications, SDMA",
                "media-desk@nirvaha.gov.in | +91-80-2200-9999",
                "Published",
            ),
            (
                "SITREP-2026-09-002",
                "Press Bulletin: 5 Emergency Relief Camps Activated with Free Rations and Medical Aid in HSR Layout and Koramangala",
                "HSR Layout",
                "Flash Flood / Drainage Surcharge",
                "SDMA in partnership with the Indian Red Cross and Goonj has operationalized five high-ground relief shelters equipped with potable drinking water, clean bedding, and pediatric supplies. Admission is open 24/7 with zero documentation requirements for affected citizens. Emergency ambulances (108) and NDRF boat teams remain stationed.",
                0,
                520,
                390,
                "Principal Disaster Relief Commissioner",
                "media-desk@nirvaha.gov.in | +91-80-2200-9999",
                "Published",
            ),
        ]
        cur.executemany(
            """INSERT INTO press_releases (bulletin_no, headline, zone, disaster_type, official_statement, verified_casualties, evacuees_count, sheltered_count, spokesperson, media_contact, status)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            demo_press,
        )

    # Sync existing users into WhatsApp Official Broadcast Channel
    try:
        cur.execute("""
            INSERT OR IGNORE INTO whatsapp_subscribers (user_id, name, phone, zone, channel_name, status)
            SELECT id, name, phone, zone, 'Nirvaha Official Emergency Broadcast', 'Subscribed'
            FROM users WHERE phone IS NOT NULL AND phone != ''
        """)
        conn.commit()
    except Exception:
        pass

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


def ensure_real_hospitals_for_zone(zone_name, lat, lon):
    """Discovers and adds genuine, accredited real hospitals for the zone. Guarantees no duplicates."""
    from utils.hospital_search import get_real_hospitals_for_location
    real_hospitals = get_real_hospitals_for_location(zone_name, lat, lon)
    for h in real_hospitals:
        existing = query(
            "SELECT id FROM hospitals WHERE LOWER(TRIM(hospital_name)) = LOWER(TRIM(?)) AND LOWER(TRIM(location)) = LOWER(TRIM(?))",
            (h["hospital_name"], zone_name),
            fetchone=True,
        )
        if not existing:
            query(
                """INSERT OR IGNORE INTO hospitals
                   (hospital_name, location, lat, lon, beds_total, beds_available,
                    doctors_available, ambulances_available, icu_available, contact)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    h["hospital_name"],
                    zone_name,
                    h["lat"],
                    h["lon"],
                    h["beds_total"],
                    h["beds_available"],
                    h["doctors_available"],
                    h["ambulances_available"],
                    h["icu_available"],
                    h["contact"],
                ),
            )


def add_zone(name, lat, lon, density):
    normalized = (name or "").strip()
    if not normalized:
        raise ValueError("Zone name is required.")

    existing = query(
        "SELECT id FROM zones WHERE LOWER(name) = LOWER(?)",
        (normalized,),
        fetchone=True,
    )
    if existing:
        zone_id = existing["id"]
        ensure_real_hospitals_for_zone(normalized, lat, lon)
        return zone_id

    zone_id = query(
        "INSERT INTO zones (name, lat, lon, density) VALUES (?, ?, ?, ?)",
        (normalized, lat, lon, density),
    )

    # 1. Auto-discover and add genuine accredited real hospitals (zero duplicates, no fake hospitals)
    ensure_real_hospitals_for_zone(normalized, lat, lon)

    # 2. Rescue team
    query(
        "INSERT INTO rescue_teams (team_name, zone, lat, lon, vehicles, ambulances, fire_units, personnel, status) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'available')",
        (
            f"{normalized} Rapid Response",
            normalized,
            lat,
            lon,
            8,
            3,
            2,
            18,
        ),
    )

    # 3. Relief shelter
    existing_shelter = query("SELECT id FROM shelters WHERE LOWER(zone) = LOWER(?)", (normalized,), fetchone=True)
    if not existing_shelter:
        query(
            "INSERT INTO shelters (name, zone, lat, lon, capacity, current_occupancy, contact) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                f"{normalized} Relief Shelter",
                normalized,
                round(lat + 0.005, 4),
                round(lon + 0.005, 4),
                600,
                0,
                "108",
            ),
        )

    # 4. Zone state
    query(
        "INSERT INTO zone_state (zone, last_risk_level, updated_at) VALUES (?, ?, CURRENT_TIMESTAMP) ON CONFLICT(zone) DO NOTHING",
        (normalized, "Low"),
    )

    return zone_id


def delete_zone(zone_id):
    zone = query("SELECT * FROM zones WHERE id = ?", (zone_id,), fetchone=True)
    if not zone:
        return

    zone_name = zone["name"]
    # 1. Clear deployments linked to zone or hospitals in that zone
    query(
        "DELETE FROM deployments WHERE zone = ? OR hospital_id IN (SELECT id FROM hospitals WHERE LOWER(TRIM(location)) = LOWER(TRIM(?)))",
        (zone_name, zone_name),
    )
    # 2. Delete all hospitals belonging to this location
    query("DELETE FROM hospitals WHERE LOWER(TRIM(location)) = LOWER(TRIM(?))", (zone_name,))
    # 3. Delete shelters in this zone
    query("DELETE FROM shelters WHERE LOWER(TRIM(zone)) = LOWER(TRIM(?))", (zone_name,))
    # 4. Delete rescue teams in this zone
    query("DELETE FROM rescue_teams WHERE LOWER(TRIM(zone)) = LOWER(TRIM(?))", (zone_name,))
    # 5. Delete zone alerts, disasters, traffic, zone_state
    query("DELETE FROM alerts WHERE LOWER(TRIM(zone)) = LOWER(TRIM(?))", (zone_name,))
    query("DELETE FROM disasters WHERE LOWER(TRIM(location)) = LOWER(TRIM(?))", (zone_name,))
    query("DELETE FROM traffic WHERE LOWER(TRIM(location)) = LOWER(TRIM(?))", (zone_name,))
    query("DELETE FROM zone_state WHERE LOWER(TRIM(zone)) = LOWER(TRIM(?))", (zone_name,))
    # 6. Remove all users registered in this zone
    query("DELETE FROM users WHERE LOWER(TRIM(zone)) = LOWER(TRIM(?))", (zone_name,))
    # 7. Delete the zone itself
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


def get_all_hospitals():
    return query("SELECT * FROM hospitals ORDER BY hospital_name")


def update_hospital_capacity(hospital_id, beds_available=None, icu_available=None, doctors_available=None, ambulances_available=None):
    fields = []
    values = []
    if beds_available is not None:
        fields.append("beds_available = ?")
        values.append(beds_available)
    if icu_available is not None:
        fields.append("icu_available = ?")
        values.append(icu_available)
    if doctors_available is not None:
        fields.append("doctors_available = ?")
        values.append(doctors_available)
    if ambulances_available is not None:
        fields.append("ambulances_available = ?")
        values.append(ambulances_available)
    if not fields:
        return 0
    sql = f"UPDATE hospitals SET {', '.join(fields)} WHERE id = ?"
    values.append(hospital_id)
    return query(sql, tuple(values))



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


def create_citizen(name, email, password_hash, zone=None, phone=None):
    """Public signup always creates role='citizen' — there is no way to
    self-register as admin/sub-admin through this function; those accounts
    are only ever created by seeding or directly in the database. Starts
    unverified; see set_verification_code()/mark_email_verified()."""
    return query(
        "INSERT INTO users (name, email, password_hash, role, zone, email_verified, phone) "
        "VALUES (?, ?, ?, 'citizen', ?, 0, ?)",
        (name, email, password_hash, zone, phone),
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
        "SELECT id, name, email, phone, zone, email_verified, last_seen, created_at "
        "FROM users WHERE role = 'citizen' ORDER BY created_at DESC LIMIT ?",
        (limit,),
    )


# ------------------------------------------------------- notifications -----

def log_notification(channel, recipient, title, message, zone=None, status="sent", alert_id=None):
    return query(
        """INSERT INTO notifications (channel, recipient, title, message, zone, status, alert_id)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (channel, recipient, title, message, zone, status, alert_id),
    )


def get_recent_notifications(limit=50):
    return query("SELECT * FROM notifications ORDER BY id DESC LIMIT ?", (limit,))


def get_users_by_zone(zone_name):
    return query("SELECT * FROM users WHERE LOWER(zone) = LOWER(?)", (zone_name,))


def get_all_contactable_users():
    return query("SELECT * FROM users WHERE (email IS NOT NULL AND email != '') OR (phone IS NOT NULL AND phone != '')")


# ---------------------------------------------------- system settings -----

def get_setting(key, default=None):
    row = query("SELECT value FROM system_settings WHERE key = ?", (key,), fetchone=True)
    return row["value"] if row else default


def set_setting(key, value):
    query(
        """INSERT INTO system_settings (key, value, updated_at)
           VALUES (?, ?, CURRENT_TIMESTAMP)
           ON CONFLICT(key) DO UPDATE SET
             value = excluded.value,
             updated_at = CURRENT_TIMESTAMP""",
        (key, str(value)),
    )


def is_auto_dispatch_enabled():
    val = get_setting("auto_dispatch_enabled", None)
    if val is None:
        return getattr(Config, "AUTO_DISPATCH_DEFAULT", False)
    return val.lower() in ("1", "true", "yes", "on")


def set_auto_dispatch_enabled(enabled):
    set_setting("auto_dispatch_enabled", "1" if enabled else "0")


# ----------------------------------------------------------- audit log -----

def log_audit(*args, **kwargs):
    """Flexible audit logging supporting positional or keyword args:
    log_audit(user_id, user_name, role, action, target_type, target_id, details, ip_address)
    OR
    log_audit(action=..., target_type=..., target_id=..., details=..., ...)
    """
    user_id = kwargs.get("user_id")
    user_name = kwargs.get("user_name")
    role = kwargs.get("role")
    action = kwargs.get("action", "ACTION")
    target_type = kwargs.get("target_type")
    target_id = kwargs.get("target_id")
    details = kwargs.get("details")
    ip_address = kwargs.get("ip_address")

    if len(args) == 8:
        user_id, user_name, role, action, target_type, target_id, details, ip_address = args
    elif len(args) >= 4:
        if isinstance(args[0], (int,)) or (args[0] is None and len(args) >= 7):
            user_id = args[0]
            user_name = args[1]
            role = args[2]
            action = args[3]
            target_type = args[4] if len(args) > 4 else None
            target_id = args[5] if len(args) > 5 else None
            details = args[6] if len(args) > 6 else None
            ip_address = args[7] if len(args) > 7 else None
        else:
            action = args[0]
            target_type = args[1]
            target_id = args[2]
            details = args[3]
            if len(args) > 4: user_name = args[4]
            if len(args) > 5: role = args[5]
            if len(args) > 6: user_id = args[6]
            if len(args) > 7: ip_address = args[7]

    return query(
        """INSERT INTO audit_logs (user_id, user_name, role, action, target_type, target_id, details, ip_address)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (user_id, user_name, role, action, target_type, str(target_id) if target_id is not None else None, details, ip_address),
    )


def get_audit_logs(limit=100, action=None, role=None):
    clauses = []
    params = []
    if action:
        clauses.append("action = ?")
        params.append(action)
    if role:
        clauses.append("role = ?")
        params.append(role)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    sql = f"SELECT * FROM audit_logs {where} ORDER BY id DESC LIMIT ?"
    params.append(limit)
    return query(sql, tuple(params))


# ------------------------------------------------------------- shelters -----

def get_shelters(zone=None):
    if zone:
        return query("SELECT * FROM shelters WHERE LOWER(zone) = LOWER(?) ORDER BY name", (zone,))
    return query("SELECT * FROM shelters ORDER BY zone, name")


# ------------------------------------------------------- bulk data import -----

def bulk_insert_zones(rows):
    """rows: list of dicts with keys name, lat, lon, density"""
    conn = get_connection()
    cur = conn.cursor()
    imported = 0
    errors = []

    for idx, r in enumerate(rows, start=1):
        name = (r.get("name") or "").strip()
        if not name:
            errors.append(f"Row {idx}: Name missing.")
            continue
        try:
            lat = float(r.get("lat", 0))
            lon = float(r.get("lon", 0))
            density = float(r.get("density", 0.5))
            cur.execute(
                """INSERT INTO zones (name, lat, lon, density)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(name) DO UPDATE SET lat=excluded.lat, lon=excluded.lon, density=excluded.density""",
                (name, lat, lon, density),
            )
            # Ensure zone_state exists
            cur.execute("INSERT INTO zone_state (zone, last_risk_level) VALUES (?, 'Low') ON CONFLICT(zone) DO NOTHING", (name,))
            imported += 1
        except Exception as e:
            errors.append(f"Row {idx} ({name}): {e}")

    conn.commit()
    conn.close()

    # Automatically ensure real hospitals exist for imported zones
    for r in rows:
        name = (r.get("name") or "").strip()
        if name:
            try:
                lat = float(r.get("lat", 0))
                lon = float(r.get("lon", 0))
                ensure_real_hospitals_for_zone(name, lat, lon)
            except Exception:
                pass

    return {"imported": imported, "errors": errors}


def bulk_insert_hospitals(rows):
    """rows: list of dicts with hospital_name, location, lat, lon, beds_total, beds_available, doctors, ambulances, icu, contact"""
    conn = get_connection()
    cur = conn.cursor()
    imported = 0
    errors = []

    for idx, r in enumerate(rows, start=1):
        name = (r.get("hospital_name") or "").strip()
        location = (r.get("location") or "").strip()
        if not name or not location:
            errors.append(f"Row {idx}: hospital_name or location missing.")
            continue
        try:
            existing = cur.execute(
                "SELECT id FROM hospitals WHERE LOWER(TRIM(hospital_name)) = LOWER(TRIM(?)) AND LOWER(TRIM(location)) = LOWER(TRIM(?))",
                (name, location),
            ).fetchone()
            if existing:
                h_id = existing["id"] if isinstance(existing, dict) else existing[0]
                cur.execute(
                    """UPDATE hospitals SET
                        lat = ?, lon = ?, beds_total = ?, beds_available = ?,
                        doctors_available = ?, ambulances_available = ?, icu_available = ?, contact = ?
                       WHERE id = ?""",
                    (
                        float(r.get("lat", 12.9)),
                        float(r.get("lon", 77.6)),
                        int(r.get("beds_total", 100)),
                        int(r.get("beds_available", 20)),
                        int(r.get("doctors_available", 15)),
                        int(r.get("ambulances_available", 4)),
                        int(r.get("icu_available", 8)),
                        r.get("contact", "108"),
                        h_id,
                    ),
                )
            else:
                cur.execute(
                    """INSERT INTO hospitals (hospital_name, location, lat, lon, beds_total, beds_available, doctors_available, ambulances_available, icu_available, contact)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        name,
                        location,
                        float(r.get("lat", 12.9)),
                        float(r.get("lon", 77.6)),
                        int(r.get("beds_total", 100)),
                        int(r.get("beds_available", 20)),
                        int(r.get("doctors_available", 15)),
                        int(r.get("ambulances_available", 4)),
                        int(r.get("icu_available", 8)),
                        r.get("contact", "108"),
                    ),
                )
            imported += 1
        except Exception as e:
            errors.append(f"Row {idx} ({name}): {e}")

    conn.commit()
    conn.close()
    return {"imported": imported, "errors": errors}


def bulk_insert_teams(rows):
    """rows: list of dicts with team_name, zone, lat, lon, vehicles, ambulances, fire_units, personnel, status"""
    conn = get_connection()
    cur = conn.cursor()
    imported = 0
    errors = []

    for idx, r in enumerate(rows, start=1):
        name = (r.get("team_name") or "").strip()
        zone = (r.get("zone") or "").strip()
        if not name or not zone:
            errors.append(f"Row {idx}: team_name or zone missing.")
            continue
        try:
            cur.execute(
                """INSERT INTO rescue_teams (team_name, zone, lat, lon, vehicles, ambulances, fire_units, personnel, status)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    name,
                    zone,
                    float(r.get("lat", 12.9)),
                    float(r.get("lon", 77.6)),
                    int(r.get("vehicles", 4)),
                    int(r.get("ambulances", 2)),
                    int(r.get("fire_units", 2)),
                    int(r.get("personnel", 16)),
                    r.get("status", "available").strip().lower() or "available",
                ),
            )
            imported += 1
        except Exception as e:
            errors.append(f"Row {idx} ({name}): {e}")

    conn.commit()
    conn.close()
    return {"imported": imported, "errors": errors}


# ---------------------------------------------------- password reset -----

def create_password_reset_code(user_id, code_hash, expires_at):
    # Invalidate previous unused codes
    query("UPDATE password_resets SET used = 1 WHERE user_id = ? AND used = 0", (user_id,))
    return query(
        """INSERT INTO password_resets (user_id, reset_code_hash, expires_at, used)
           VALUES (?, ?, ?, 0)""",
        (user_id, code_hash, expires_at),
    )


def verify_password_reset_code(user_id, code_hash):
    row = query(
        """SELECT * FROM password_resets
           WHERE user_id = ? AND reset_code_hash = ? AND used = 0 AND expires_at >= CURRENT_TIMESTAMP
           ORDER BY id DESC LIMIT 1""",
        (user_id, code_hash),
        fetchone=True,
    )
    return bool(row)


def consume_password_reset(user_id, new_password_hash):
    query("UPDATE users SET password_hash = ? WHERE id = ?", (new_password_hash, user_id))
    query("UPDATE password_resets SET used = 1 WHERE user_id = ?", (user_id,))


# ---------------------------------------------------------- IoT Sensors -----

def get_all_iot_sensors():
    return query("SELECT * FROM iot_sensors ORDER BY zone, sensor_name")


def update_iot_sensor_reading(sensor_id, value, battery_pct=None):
    sensor = query("SELECT * FROM iot_sensors WHERE sensor_id = ?", (sensor_id,), fetchone=True)
    if not sensor:
        return None
    val = float(value)
    status = "Normal"
    if val >= sensor["critical_threshold"]:
        status = "Critical"
    elif val >= sensor["warning_threshold"]:
        status = "Warning"

    if battery_pct is not None:
        query(
            "UPDATE iot_sensors SET current_value = ?, status = ?, battery_pct = ?, updated_at = CURRENT_TIMESTAMP WHERE sensor_id = ?",
            (val, status, int(battery_pct), sensor_id),
        )
    else:
        query(
            "UPDATE iot_sensors SET current_value = ?, status = ?, updated_at = CURRENT_TIMESTAMP WHERE sensor_id = ?",
            (val, status, sensor_id),
        )
    return {"sensor_id": sensor_id, "current_value": val, "status": status}


# ---------------------------------------------------- Social Media SOS -----

def get_social_distress_feed(limit=30, zone=None, platform=None, verified_only=False):
    sql = "SELECT * FROM social_distress WHERE platform IN ('Twitter', 'Instagram', 'Facebook')"
    params = []
    if zone:
        sql += " AND LOWER(TRIM(zone)) = LOWER(TRIM(?))"
        params.append(zone)
    if platform:
        sql += " AND LOWER(TRIM(platform)) = LOWER(TRIM(?))"
        params.append(platform)
    if verified_only:
        sql += " AND verified = 1"
    sql += " ORDER BY created_at DESC LIMIT ?"
    params.append(limit)
    return query(sql, params)


def log_social_distress(platform, username, message, zone, urgency_level="High", sentiment=-0.7, verified=0, post_url=None):
    # Strict validation: Only Twitter, Instagram, and Facebook allowed
    plat = (platform or "Twitter").strip()
    if plat.lower() in ("twitter", "x", "twitter / x", "twitter/x"):
        plat = "Twitter"
    elif plat.lower() in ("instagram", "insta", "ig"):
        plat = "Instagram"
    elif plat.lower() in ("facebook", "fb"):
        plat = "Facebook"
    else:
        plat = "Twitter"

    return query(
        """INSERT INTO social_distress (platform, username, message, zone, urgency_level, sentiment, verified, post_url)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (plat, username, message, zone, urgency_level, sentiment, int(verified), post_url),
    )


# --------------------------------------------------- Emergency Reports -----

def create_emergency_report(reporter_name, reporter_phone, disaster_type, location, lat, lon, description, severity="High"):
    return query(
        """INSERT INTO emergency_reports (reporter_name, reporter_phone, disaster_type, location, lat, lon, description, severity, status)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'Pending')""",
        (reporter_name, reporter_phone, disaster_type, location, lat, lon, description, severity),
    )


def get_emergency_reports(limit=100, status=None, severity=None, disaster_type=None, search=None):
    sql = "SELECT * FROM emergency_reports WHERE 1=1"
    params = []
    if status and status.lower() != "all":
        sql += " AND LOWER(status) = LOWER(?)"
        params.append(status)
    if severity and severity.lower() != "all":
        sql += " AND LOWER(severity) = LOWER(?)"
        params.append(severity)
    if disaster_type and disaster_type.lower() != "all":
        sql += " AND LOWER(disaster_type) LIKE LOWER(?)"
        params.append(f"%{disaster_type}%")
    if search:
        s = f"%{search.strip()}%"
        sql += " AND (reporter_name LIKE ? OR reporter_phone LIKE ? OR location LIKE ? OR description LIKE ?)"
        params.extend([s, s, s, s])
    sql += " ORDER BY created_at DESC LIMIT ?"
    params.append(limit)
    return query(sql, params)


def get_emergency_report_by_id(report_id):
    return query("SELECT * FROM emergency_reports WHERE id = ?", (report_id,), fetchone=True)


def get_emergency_report_stats():
    total_row = query("SELECT COUNT(*) as c FROM emergency_reports", fetchone=True)
    pending_row = query("SELECT COUNT(*) as c FROM emergency_reports WHERE LOWER(status) = 'pending'", fetchone=True)
    verified_row = query("SELECT COUNT(*) as c FROM emergency_reports WHERE LOWER(status) = 'verified'", fetchone=True)
    dispatched_row = query("SELECT COUNT(*) as c FROM emergency_reports WHERE LOWER(status) = 'dispatched'", fetchone=True)
    resolved_row = query("SELECT COUNT(*) as c FROM emergency_reports WHERE LOWER(status) = 'resolved'", fetchone=True)
    critical_row = query("SELECT COUNT(*) as c FROM emergency_reports WHERE LOWER(severity) = 'critical'", fetchone=True)
    return {
        "total": total_row["c"] if total_row else 0,
        "pending": pending_row["c"] if pending_row else 0,
        "verified": verified_row["c"] if verified_row else 0,
        "dispatched": dispatched_row["c"] if dispatched_row else 0,
        "resolved": resolved_row["c"] if resolved_row else 0,
        "critical": critical_row["c"] if critical_row else 0,
    }


def get_pending_emergency_reports_count():
    row = query("SELECT COUNT(*) as c FROM emergency_reports WHERE LOWER(status) = 'pending'", fetchone=True)
    return row["c"] if row else 0


def update_emergency_report_status(report_id, status):
    return query("UPDATE emergency_reports SET status = ? WHERE id = ?", (status, report_id))


def delete_emergency_report(report_id):
    return query("DELETE FROM emergency_reports WHERE id = ?", (report_id,))


# -------------------------------------------------- relief & NGOs (Stakeholder 5) -----

def get_shelters_with_supplies(zone=None):
    shelters = get_shelters(zone)
    for s in shelters:
        supplies = query("SELECT * FROM relief_supplies WHERE shelter_id = ? ORDER BY id", (s["id"],))
        s["supplies"] = supplies
        s["occupancy_pct"] = round((s["current_occupancy"] / max(s["capacity"], 1)) * 100, 1)
        s["critical_supplies_count"] = sum(1 for sup in supplies if sup["status"] == "Critical")
    return shelters


def get_shelter_supplies(shelter_id):
    return query("SELECT * FROM relief_supplies WHERE shelter_id = ? ORDER BY id", (shelter_id,))


def update_shelter_supply(supply_id, quantity, status=None):
    if status is None:
        target = query("SELECT minimum_required FROM relief_supplies WHERE id = ?", (supply_id,), fetchone=True)
        min_req = target["minimum_required"] if target else 100
        if quantity <= (min_req * 0.3):
            status = "Critical"
        elif quantity <= min_req:
            status = "Low"
        else:
            status = "Adequate"
    query(
        "UPDATE relief_supplies SET quantity = ?, status = ?, last_updated = CURRENT_TIMESTAMP WHERE id = ?",
        (quantity, status, supply_id),
    )
    return query("SELECT * FROM relief_supplies WHERE id = ?", (supply_id,), fetchone=True)


def update_shelter_occupancy(shelter_id, current_occupancy):
    query(
        "UPDATE shelters SET current_occupancy = ? WHERE id = ?",
        (max(0, current_occupancy), shelter_id),
    )
    return query("SELECT * FROM shelters WHERE id = ?", (shelter_id,), fetchone=True)


def get_relief_volunteers(zone=None):
    if zone:
        return query("SELECT * FROM relief_volunteers WHERE LOWER(assigned_zone) = LOWER(?) ORDER BY ngo_name", (zone,))
    return query("SELECT * FROM relief_volunteers ORDER BY assigned_zone, ngo_name")


# ------------------------------------------------ media & news (Stakeholder 6) -----

def get_press_releases(published_only=True):
    if published_only:
        return query("SELECT * FROM press_releases WHERE status = 'Published' ORDER BY published_at DESC")
    return query("SELECT * FROM press_releases ORDER BY published_at DESC")


def get_press_release_by_id(release_id):
    return query("SELECT * FROM press_releases WHERE id = ?", (release_id,), fetchone=True)


def add_press_release(bulletin_no, headline, zone, disaster_type, official_statement,
                      verified_casualties=0, evacuees_count=0, sheltered_count=0,
                      spokesperson="State Disaster Management Authority",
                      media_contact="media-desk@nirvaha.gov.in", status="Published"):
    query(
        """INSERT OR REPLACE INTO press_releases
           (bulletin_no, headline, zone, disaster_type, official_statement, verified_casualties, evacuees_count, sheltered_count, spokesperson, media_contact, status)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (bulletin_no, headline, zone, disaster_type, official_statement,
         verified_casualties, evacuees_count, sheltered_count, spokesperson, media_contact, status),
    )
    return query("SELECT * FROM press_releases WHERE bulletin_no = ?", (bulletin_no,), fetchone=True)


def get_stakeholders_summary():
    """Generates an operational snapshot across all 6 External Stakeholders."""
    hospitals = query("SELECT COUNT(*) as cnt, SUM(beds_total) as tot_beds, SUM(beds_available) as avail_beds, SUM(ambulances_available) as amb FROM hospitals", fetchone=True) or {}
    shelters = query("SELECT COUNT(*) as cnt, SUM(capacity) as tot_cap, SUM(current_occupancy) as occ FROM shelters", fetchone=True) or {}
    teams = query("SELECT COUNT(*) as cnt, SUM(personnel) as staff, SUM(vehicles) as veh FROM rescue_teams", fetchone=True) or {}
    users_cnt = query("SELECT COUNT(*) as cnt FROM users WHERE role = 'citizen'", fetchone=True) or {}
    volunteers = query("SELECT COUNT(*) as ngo_cnt, SUM(active_volunteers) as total_volunteers FROM relief_volunteers", fetchone=True) or {}
    press_cnt = query("SELECT COUNT(*) as cnt FROM press_releases WHERE status = 'Published'", fetchone=True) or {}
    incidents_cnt = query("SELECT COUNT(*) as cnt FROM emergency_reports", fetchone=True) or {}

    return {
        "authorities": {
            "name": "Disaster Management Authorities",
            "role": "Central Command & Policy Oversight",
            "status": "Active 24x7",
            "active_dashboards": 1,
        },
        "emergency_services": {
            "name": "Emergency Services (Police, Fire, Rescue)",
            "role": "Tactical Ground Deployment & Evacuation",
            "status": "Ready",
            "active_teams": teams.get("cnt") or 0,
            "field_personnel": teams.get("staff") or 0,
            "rescue_vehicles": teams.get("veh") or 0,
        },
        "hospitals": {
            "name": "Hospitals & Medical Facilities",
            "role": "Emergency Triage & Trauma Bed Allocation",
            "status": "Operational",
            "hospital_count": hospitals.get("cnt") or 0,
            "available_beds": hospitals.get("avail_beds") or 0,
            "ambulances": hospitals.get("amb") or 0,
        },
        "citizens": {
            "name": "Public / Citizens",
            "role": "Evacuation Route Navigation & SOS Hazard Reporting",
            "status": "Connected",
            "registered_citizens": users_cnt.get("cnt") or 0,
            "citizen_reports_filed": incidents_cnt.get("cnt") or 0,
        },
        "ngos": {
            "name": "NGOs & Relief Organizations",
            "role": "Emergency Shelter Management & Mass Ration Supplies",
            "status": "Mobilized",
            "partner_ngos": volunteers.get("ngo_cnt") or 0,
            "active_volunteers": volunteers.get("total_volunteers") or 0,
            "relief_shelters": shelters.get("cnt") or 0,
            "shelter_capacity": shelters.get("tot_cap") or 0,
            "current_evacuees": shelters.get("occ") or 0,
        },
        "media": {
            "name": "Media & News Agencies",
            "role": "Public Broadcast Transmission & Official Press Advisories",
            "status": "Broadcasting",
            "published_releases": press_cnt.get("cnt") or 0,
            "bulletin_route": "/press",
        },
    }


# ----------------------------------------------- WhatsApp Broadcast Channel -----

def enroll_whatsapp_subscriber(user_id, name, phone, zone=None, channel_name="Nirvaha Official Emergency Broadcast"):
    """Enrolls a user's phone number into the official WhatsApp emergency broadcast channel."""
    clean_p = str(phone).strip()
    query(
        """INSERT INTO whatsapp_subscribers (user_id, name, phone, zone, channel_name, status)
           VALUES (?, ?, ?, ?, ?, 'Subscribed')
           ON CONFLICT(phone) DO UPDATE SET
             user_id = excluded.user_id,
             name = excluded.name,
             zone = excluded.zone,
             status = 'Subscribed'""",
        (user_id, name, clean_p, zone, channel_name),
    )
    return query("SELECT * FROM whatsapp_subscribers WHERE phone = ?", (clean_p,), fetchone=True)


def get_whatsapp_subscribers(zone=None):
    """Returns all active subscribers enrolled in the official WhatsApp broadcast channel."""
    if zone:
        return query(
            "SELECT * FROM whatsapp_subscribers WHERE status = 'Subscribed' AND (LOWER(zone) = LOWER(?) OR zone IS NULL OR zone = '') ORDER BY name",
            (zone,),
        )
    return query("SELECT * FROM whatsapp_subscribers WHERE status = 'Subscribed' ORDER BY zone, name")


def get_whatsapp_subscribers_count():
    row = query("SELECT COUNT(*) as c FROM whatsapp_subscribers WHERE status = 'Subscribed'", fetchone=True)
    return row["c"] if row else 0


def update_whatsapp_subscriber_alert_timestamp(phone):
    query(
        "UPDATE whatsapp_subscribers SET last_alert_sent = CURRENT_TIMESTAMP WHERE phone = ?",
        (str(phone).strip(),),
    )







