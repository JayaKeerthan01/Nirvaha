# Nirvaha — Intelligent Multi-Agent Disaster Response System

A working Flask application that implements the multi-agent disaster response
architecture: a **Weather Agent**, **Traffic Agent**, **Hospital Agent**, and
**Rescue Agent**, all coordinated by a **Coordinator Agent** that feeds a
live command dashboard.

It runs completely standalone — no API keys, no external database server,
and no internet connection required. Weather and traffic data are generated
by realistic simulators so every screen has live, constantly-updating data
from the moment you start it. Real API keys can be dropped in later (see
[Going live with real data](#going-live-with-real-data)) without changing
any other code.

**Branding.** The name and logo are "Nirvaha" (protection / safe passage) —
a shield containing a winding path to a destination point, echoing the
evacuation-routing feature. The mark lives at
`static/img/logo-mark.svg` (used as the favicon); the same shape is inlined
directly in `templates/base.html`, `templates/citizen_base.html`, and
`templates/index.html` next to the wordmark so it can pick up the
gradient badge styling (`.brand-mark` in `static/css/style.css`) without an
extra image request. To rebrand again later, that's the full list of
places to touch — there's no other copy of the logo or name hiding
anywhere else in the codebase.

---

## 1. What's inside

| Layer | Technology | Where |
|---|---|---|
| Frontend | HTML, custom CSS, vanilla JS, Leaflet.js (maps), Chart.js (charts) | `templates/`, `static/` |
| Backend | Flask | `app.py` |
| Multi-agent system | Plain Python classes | `agents/` |
| Machine learning | scikit-learn (RandomForest) | `ml/` |
| Database | SQLite (zero setup) | `database/` |
| Simulated external APIs | Weather + Maps, with real-API fallback hooks | `api/` |

```
Disaster-Response-System/
├── app.py                     Flask app + all routes/API endpoints
├── config.py                  Central config: zones, paths, API keys, thresholds
├── requirements.txt
│
├── agents/
│   ├── weather_agent.py       Predicts flood/cyclone/landslide risk per zone
│   ├── traffic_agent.py       Predicts congestion, finds safest evacuation routes
│   ├── hospital_agent.py      Ranks hospitals by capacity + distance; reserves/releases beds
│   ├── rescue_agent.py        Prioritizes zones, assigns + deploys/recalls rescue teams
│   ├── coordinator_agent.py   Combines all four into one dashboard payload
│   └── scoring.py             Pure suitability/priority scoring functions (unit tested)
│
├── utils/
│   └── geo.py                 Shared haversine distance helper
│
├── ml/
│   ├── generate_datasets.py   Builds synthetic training data (swap for real history)
│   ├── disaster_prediction.py Model 1: rainfall/temp/humidity/wind -> disaster type
│   └── traffic_prediction.py  Model 2: hour/location/weather -> congestion level
│
├── api/
│   ├── weather_api.py         Simulated live weather (or real OpenWeather calls)
│   └── maps_api.py            Route/distance calculation (or real Google Maps calls)
│
├── database/
│   └── db.py                  SQLite schema, seed data, query helpers
│
├── templates/                 Jinja2 pages (dashboard, predictions, traffic, hospitals,
│                               rescue, alerts, admin_zones, login, signup, citizen_*)
├── static/css/style.css       Design system ("Nirvaha" command-center theme)
├── static/js/                 Per-page dashboard logic (polls the JSON API every 15s)
├── datasets/                  Generated training CSVs
├── models/                    Trained model files (.joblib)
└── tests/                     Unit tests for geo + scoring (stdlib unittest, no extra deps)
```

---

## 2. Setup

**Requirements:** Python 3.10+

```bash
cd Disaster-Response-System
python3 -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

## 3. Generate data and train the models (first time only)

```bash
python -m ml.generate_datasets      # writes datasets/weather_history.csv + traffic_history.csv
python -m ml.disaster_prediction    # trains + saves models/disaster_model.joblib
python -m ml.traffic_prediction     # trains + saves models/traffic_model.joblib
```

> These two model files are already included in this download, so this step
> is only needed if you delete them, change the training data, or want to
> retrain from scratch.

## 4. Run it

```bash
python app.py
```

Open **http://127.0.0.1:5000** in your browser.

**Demo logins**
```
Admin (full access, incl. /admin/zones):
  email:    admin@disaster-response.local
  password: admin123

Operator (dashboard + deploy/recall, no zone management):
  email:    operator@disaster-response.local
  password: operator123

Resident (public citizen portal only, /citizen):
  email:    resident@disaster-response.local
  password: resident123
```

The database (`database/disaster_response.db`) and its demo hospitals/rescue
teams/users/zones are created automatically the first time you run `app.py`.

---

## 4. What each page does

- **Overview (`/dashboard`)** — the Coordinator Agent's combined picture: overall risk level, worst weather/traffic/hospital/rescue status, a zone priority ranking, deployment recommendations (with a **Deploy** button — see below), and a live Leaflet map.
- **Disaster Prediction (`/predictions`)** — per-zone flood/cyclone/landslide probability from the Weather Agent's ML model, with a live bar chart per zone.
- **Traffic & Routes (`/traffic`)** — congestion level and road status per zone, plus a "find safest route" tool that draws an evacuation path on the map.
- **Hospitals (`/hospitals`)** — bed/ICU/doctor/ambulance availability across every facility. Availability now actually changes when a deployment reserves beds (see below), instead of being a static number forever.
- **Rescue Ops (`/rescue`)** — team status, zone priority ranking, the auto-generated deployment plan with a **Deploy** action, and an **Active deployments** table with a **Recall** action.
- **Alerts (`/alerts`)** — the auto-generated High-risk alert log, plus a full **Disaster event log** below it showing every Weather Agent assessment (the `disasters` table).
- **Manage Zones (`/admin/zones`, admin only)** — add or remove monitored zones at runtime. Every agent reads zones from the database, so a new zone shows up on the very next 15-second refresh — no code change or restart required.
- **Citizen portal (`/citizen`, any logged-in account)** — a separate, simpler public UI: plain-language risk cards, an evacuation-route finder, a hospital directory with tap-to-call, and a chatbot. Anyone can create an account at `/signup`. See "Citizen portal" below.

All pages poll their JSON API every 15 seconds (`/api/dashboard`, `/api/weather`, `/api/traffic`, `/api/hospitals`, `/api/rescue`, `/api/alerts`, `/api/incidents`) so the whole dashboard feels live without a page refresh. Change `POLL_INTERVAL_MS` in `config.py` to adjust that.

### Deploy / Recall — recommendations that actually do something

The Rescue Agent has always *recommended* a nearest available team per
at-risk zone. Clicking **Deploy** on that recommendation now:

1. Marks the nearest available team's `status` as `deployed` (it drops out
   of future recommendations until recalled).
2. Reserves `Config.BEDS_RESERVED_PER_DEPLOYMENT` beds (default 5) at the
   best-suited hospital for that zone.
3. Logs a Medium-severity alert noting who dispatched it.

Clicking **Recall** on an active deployment reverses all three — the team
becomes available again and the beds are released. This state lives in the
`deployments` table (see `database/db.py`), so it survives a server
restart.

### Citizen portal — a separate, simpler public front end

Anyone can create a free account at **`/signup`** — no admin approval
needed. This always creates a `citizen` role account; there's no form
field or trick that grants operator/admin access through self-registration.

Citizen accounts see a completely different, much simpler surface at
**`/citizen`**, styled for a general audience rather than an operations
team:

- **`/citizen`** — plain-language risk per zone ("All clear" / "Stay
  alert" / "Take action" instead of raw ML output)
- **`/citizen/evacuation`** — pick your area, get the safest direction and
  an ETA
- **`/citizen/hospitals`** — hospital list with tap-to-call contact buttons
  and live bed counts
- **`/citizen/chat`** ("Ask Nirvaha") — a chatbot answering plain-language
  questions about risk, evacuation, and hospitals

**Important — this closed a real access-control gap.** Before this
feature, `/dashboard`, `/rescue`, and even `/api/deploy`/`/api/recall` were
only gated by `login_required` (any authenticated user). That was fine
when only admin/operator accounts existed, but the moment `/signup` made
account creation public, a citizen would have had the same power to
dispatch real rescue teams as an admin. Fixed with a new `staff_required`
decorator (admin or operator, not citizen) now applied to the entire ops
command center — see `app.py` for the full list of routes it protects.

**The chatbot** (`agents/citizen_chat_agent.py`) checks three layers, in
order:

1. **Meta / small talk** — greetings, "what can you do", thanks, goodbye.
2. **Live-data lookups** — risk, evacuation, hospitals, emergency contacts.
   Answered only from the same data the rest of the app already computes —
   it can't invent a risk level, hospital name, or phone number that isn't
   actually in the database.
3. **General safety knowledge** (`FAQ_TOPICS` in that file) — emergency
   kits, water purification, power outages, basic first aid, and
   flood/cyclone/landslide/earthquake prep. This is genuinely static
   information, not live data, and the UI labels it "General safety info,
   not live data" rather than implying it was checked against current
   conditions.

Two modes, matching the simulate-by-default pattern used elsewhere in this
project:

- **Default** — a small, zero-dependency keyword/intent matcher covering
  all three layers above. Always available, fully deterministic, unit
  tested (`tests/test_citizen_chat.py`) — including a regression test for
  a real bug caught while building this: a question like "how do I purify
  water during a flood" was originally swallowed by the live-data risk
  lookup (which matched on the word "flood") before ever reaching the
  water-purification answer. Fixed by only treating a hazard word as a
  live-data risk query when it's paired with an actual zone name.
- **`ANTHROPIC_API_KEY` set** — the same live context, plus a note that
  general safety questions are fair game to answer from Claude's own
  knowledge, is handed to Claude for a more natural free-form answer.
  Still constrained to only use the live data for anything zone/hospital/
  route-specific. Falls back to the rule-based matcher on any failure, so
  the chatbot never just breaks.

**The suggestion chips are dynamic, not hardcoded** (`static/js/citizen_chat.js::buildSuggestions`).
On page load they call `/api/weather` and build around:
- The person's own home zone (set at signup, `users.zone`) if they have
  one — "Risk in HSR Layout (your area)".
- Otherwise, or in addition, whichever zone currently has the highest
  live risk score — so a genuinely urgent situation elsewhere surfaces
  even for someone whose own area is fine right now.
- Two of the six general-safety topics, shuffled on every load, so a
  returning visitor sees different ones instead of the same two chips
  forever.

---

## 5. How the "AI" actually works

**Model 1 — Disaster Prediction** (`ml/disaster_prediction.py`)
A `RandomForestClassifier` trained on rainfall, temperature, humidity, and
wind speed, predicting **Flood**, **Cyclone**, **Landslide**, or **Normal**,
with a probability for each. The Weather Agent turns the combined
non-normal probability into a **Low / Medium / High** risk level per zone.

**Model 2 — Traffic Prediction** (`ml/traffic_prediction.py`)
A second `RandomForestClassifier` trained on hour-of-day, zone, and weather
condition, predicting congestion as **Low / Moderate / High / Severe**. The
Traffic Agent maps that to a plain-language road status and feeds it into
route selection.

**Rescue prioritization** (`agents/rescue_agent.py`)
A weighted score (65% disaster severity + 35% population density) ranks
zones, then the nearest still-unassigned rescue team (by haversine distance)
is recommended for each zone that needs one.

**Hospital ranking** (`agents/hospital_agent.py`)
Combines bed/ICU/doctor availability with distance from the affected zone
into a single suitability score.

> **Note on earthquakes:** the original synopsis mentions earthquake
> probability, but earthquakes aren't predictable from weather data — they
> need seismic sensor input (accelerometers, historical fault-line data).
> The prediction page explains this rather than faking a number. If you want
> to add it, plug a seismic data feed into a new `ml/earthquake_prediction.py`
> following the same pattern as the two existing models.

The training data in `datasets/` is **synthetic** (generated by
`ml/generate_datasets.py` from plausible physical relationships, not real
historical records) so the project runs immediately with no external
dataset. Swap those two CSVs for real historical weather/traffic records
whenever you have them — nothing else needs to change, since both training
scripts only care about the column names.

---

## 6. Going live with real data

Everything currently runs in **simulation mode**. To connect real services:

1. **Weather — OpenWeatherMap**
   Get a free key at https://openweathermap.org/api, then set:
   ```bash
   export OPENWEATHER_API_KEY="your-key-here"
   ```
   `api/weather_api.py` will automatically start calling the real API instead
   of the simulator, and silently falls back to simulation if the call fails.

2. **Maps & routing — Google Maps Directions API**
   Get a key at https://console.cloud.google.com/google/maps-apis, then set:
   ```bash
   export GOOGLE_MAPS_API_KEY="your-key-here"
   ```
   `api/maps_api.py` will call the real Directions API for distance/duration
   (route polyline decoding is left as a small follow-up — see the comment
   in that file).

3. **Citizen chatbot — Claude API (optional)**
   Get a key at https://console.anthropic.com, then set:
   ```bash
   export ANTHROPIC_API_KEY="your-key-here"
   pip install anthropic
   ```
   `agents/citizen_chat_agent.py` will use Claude for more natural
   free-form answers instead of the keyword matcher — same live data
   either way, and it silently falls back to the matcher if the call fails.

3. **A production database.** SQLite is fine for a demo or small deployment.
   For MySQL/PostgreSQL, only `database/db.py` needs to change — every other
   file calls the `query()` / `log_alert()` / `init_db()` functions in that
   one module, never the database directly.

4. **Real hospitals/rescue teams.** Edit the seed data in
   `database/db.py::_seed_demo_data()`, or write directly to the `hospitals`
   and `rescue_teams` tables from an admin panel you build on top of this.

5. **Your own city.** Add/remove zones at runtime from `/admin/zones` as an
   admin user, or edit `Config.DEFAULT_ZONES` in `config.py` to change what
   gets seeded on first run.

---

## 7. Extending it further

This matches the "further enhancements" in the original project brief:

- **IoT sensors** — add a new `agents/sensor_agent.py` that ingests readings
  the same way `weather_agent.py` ingests `api/weather_api.py` data.
- **Satellite imagery / drone feeds** — new API module + a page that overlays
  imagery tiles on the existing Leaflet map.
- **Social media monitoring** — a new agent that scores incoming text for
  disaster-related signals and writes to the `alerts` table via `log_alert()`.
- **Cloud deployment** — the app is stateless except for SQLite, so it
  deploys cleanly to any host that runs Python (a managed Postgres instance
  is recommended over SQLite once you're multi-instance).

---

## 8. Troubleshooting

- **"Address already in use"** — another process is on port 5000; run
  `python app.py` after editing the last line of `app.py` to a different
  `port=...`, or stop the other process.
- **Blank map** — the map tiles load from a public CDN
  (`basemaps.cartocdn.com`) and Leaflet from `unpkg.com`; you need internet
  access in the *browser*, even though the Flask backend itself needs none.
- **Model file not found** — run `python -m ml.disaster_prediction` and
  `python -m ml.traffic_prediction` once; both auto-train on first use anyway,
  so this only matters if the `models/` folder is missing entirely.
- **"Too many failed sign-in attempts"** — the login rate limiter locked out
  your IP for `LOGIN_LOCKOUT_WINDOW_MINUTES` (default 15) after
  `LOGIN_MAX_FAILED_ATTEMPTS` (default 5) failed attempts. This is tracked
  in the `login_attempts` table and survives a restart by design — delete
  old rows from that table (or the whole `.db` file in dev) to reset it.
- **400 "Missing or invalid CSRF token"** — you're calling a POST endpoint
  without the token. Browser use is unaffected (templates inject it
  automatically); if you're scripting against the API, `GET` a page first
  to establish a session, read the token from either the hidden
  `csrf_token` form field or the `<meta name="csrf-token">` tag, and send
  it back as `X-CSRFToken` (JSON) or `csrf_token` (form POST).

---

## 9. Changelog — hardening pass

Started as a working demo; this pass went through it end-to-end for
production-readiness issues, dead schema, and static "recommendations" that
never became real actions. Verified by actually running the server and
exercising every new code path with real HTTP requests (see `tests/` for
the pure-function unit tests, which run with zero extra dependencies via
`python -m unittest discover -s tests`).

**Security**
- `debug=True` was hardcoded in `app.py`, leaving the Werkzeug interactive
  debugger reachable. Now off unless `FLASK_DEBUG=1`.
- No CSRF protection existed. Added a session-bound token (`csrf_token()` in
  templates, validated in `app.py::csrf_protect`).
- The `role` column existed but nothing enforced it. Added `admin_required`
  and used it on `/admin/zones`; a second demo `operator` account is seeded
  to show the difference.
- `/login` had no rate limiting against a documented demo password. Added a
  per-IP lockout backed by a new `login_attempts` table.
- Session cookies are now `HttpOnly` + `SameSite=Lax` by default, with
  `SESSION_COOKIE_SECURE` available via env var once behind HTTPS.

**Architecture**
- The `_last_alerted_level` alert-dedup dict was in-memory, so it reset on
  restart and would diverge across multiple worker processes. Moved to a
  new `zone_state` DB table.
- The `disasters` table existed in the schema but nothing ever wrote to it.
  `weather_agent` now logs every assessment there; see it on `/alerts`.
- Deployment "recommendations" never changed any state — the same
  suggestion repeated forever. Added `rescue_agent.deploy()`/`recall()`
  (backed by a new `deployments` table) that actually flips
  `rescue_teams.status` and reserves/releases `hospitals.beds_available`.
- `_distance_km` (haversine) was duplicated in `hospital_agent.py` and
  `rescue_agent.py`. Consolidated into `utils/geo.py`.
- Suitability/priority scoring was inlined inside the agent classes,
  making it untestable without a live DB. Pulled into pure functions in
  `agents/scoring.py`, unit tested in `tests/test_scoring.py`.
- Zones lived only in `Config.ZONES`, requiring a code change + redeploy
  to add a district. Now in a `zones` DB table, seeded from
  `Config.DEFAULT_ZONES`, manageable at runtime from `/admin/zones`.
- Silent `except: pass` around the OpenWeather/Google Maps live-API calls
  meant a dead key would fail over to simulation with zero visibility.
  Now logged via the standard `logging` module.
- Added `tests/` (stdlib `unittest`, no new dependency) covering the geo
  and scoring pure functions.

**Frontend robustness**
- `dashboard.js`/`traffic.js` crashed the entire page (not just the map)
  when the Leaflet CDN was blocked or slow — `initMap()` threw before
  `refreshDashboard()` ever ran. Now wrapped so a map/chart failure only
  affects that one panel; same fix applied to `predictions.js` and
  Chart.js. Caught by actually running this in a sandboxed browser with no
  outbound internet access — a real scenario for anyone behind a
  restrictive proxy or ad-blocker, not just this environment.

**Citizen portal (public-facing addition)**
- Added self-service signup (`/signup`, always creates a `citizen`-role
  account) and a separate, simpler public UI at `/citizen/*`: plain-language
  risk cards, an evacuation-route finder, a hospital directory with
  tap-to-call buttons, and a chatbot (`agents/citizen_chat_agent.py`).
- The chatbot answers only from the same live data the other agents
  already produce — no separate knowledge base to drift out of sync — with
  a zero-dependency keyword matcher by default and an optional Claude API
  upgrade (`ANTHROPIC_API_KEY`) for more natural free-form answers.
- **Making signup public exposed a real access-control gap**: `/dashboard`,
  `/rescue`, and `/api/deploy`/`/api/recall` were only behind
  `login_required` (any authenticated account), which was fine while only
  admin/operator accounts existed. Once anyone could self-register, a
  citizen account would have had the same power to dispatch real rescue
  teams as an admin. Fixed with a new `staff_required` decorator
  (admin/operator only) now guarding the entire ops command center —
  verified with real requests that a citizen account gets redirected, not
  served, from every ops route and action.
- `users.zone` (nullable) added via an idempotent migration so existing
  database files upgrade in place without deleting them.

## 10. Roadmap — not done in this pass, and why

These were flagged as worth doing but need infrastructure/credentials this
environment doesn't have, so they're documented rather than half-built:

- **Real-time push (WebSockets)** instead of 15s polling — natural fit is
  Flask-SocketIO + eventlet/gevent, which are additional dependencies.
- **SMS/push alerts to field teams** — needs a Twilio (or similar) account
  and API key.
- **Earthquake prediction agent** — genuinely needs a seismic sensor feed;
  weather data can't predict earthquakes (this was already called out in
  the original version of this README, and remains true).
- **Docker packaging** — straightforward, just not done here; the app has
  no external service dependencies so a `Dockerfile` + `docker-compose.yml`
  would be a small addition.
- **PDF export of the deployment plan** — a good next feature, skipped for
  scope in this pass.
- **Chat history persistence** — the citizen chatbot is currently stateless
  per page load (no saved conversation log). Would need a `chat_messages`
  table keyed by user id; skipped to keep this pass's scope bounded.
