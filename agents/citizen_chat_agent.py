"""
Citizen Chat Agent

Answers public questions in three layers, checked in order:
  1. Meta / small talk — greetings, "what can you do", thanks, goodbye.
  2. Live-data lookups — risk, evacuation routes, hospitals, emergency
     contacts. These pull from the same agents the dashboard uses, so an
     answer can never drift from what the rest of the site is showing.
  3. General safety knowledge (FAQ_TOPICS) — emergency kits, water
     purification, power outages, first aid basics, and per-hazard prep
     (flood/cyclone/landslide/earthquake). This is genuinely static
     knowledge, not live data, so it's kept in a clearly-labeled
     dictionary below rather than mixed in with the live lookups.
  4. Fallback — lists what it can actually help with, rather than a bare
     "I don't understand."

Two modes, matching the simulate-by-default pattern used elsewhere in this
project (api/weather_api.py, api/maps_api.py):
  - Default: the layered keyword matcher above. Zero dependencies, zero
    network calls, always available, fully deterministic (easy to test —
    see tests/test_citizen_chat.py).
  - If Config.ANTHROPIC_API_KEY is set: the same live context plus a note
    that general safety knowledge is fair game is handed to Claude for a
    more natural free-form answer. Falls back to the layered matcher on
    any failure, so the chatbot never just breaks.
"""

import re

from config import Config
from database.db import get_zones
from agents.weather_agent import weather_agent
from agents.traffic_agent import traffic_agent
from agents.hospital_agent import hospital_agent
from agents.rescue_agent import rescue_agent

try:
    import anthropic
except ImportError:
    anthropic = None


# ------------------------------------------------------- general knowledge -
# Static disaster-safety information — not live data, doesn't change per
# request, so it's fine to hardcode. Kept separate from the live-data
# lookups below so the "only ever from live data" guarantee in the module
# docstring still holds for anything zone/hospital/route-specific.

FAQ_TOPICS = {
    "emergency_kit": {
        "keywords": ["emergency kit", "go bag", "grab bag", "what to pack", "what should i pack", "go-bag", "supplies list"],
        "answer": (
            "A basic emergency kit: 3 days of water (about 4 litres per person "
            "per day), non-perishable food, a flashlight with spare batteries, "
            "a battery or hand-crank radio, a first-aid kit, any regular "
            "medications, copies of ID and insurance papers in a waterproof "
            "bag, a phone power bank, some cash, and a whistle to signal for "
            "help. Keep it somewhere you can grab in under a minute."
        ),
    },
    "flood_safety": {
        "keywords": ["flood safety", "flooding tips", "what to do in a flood", "flash flood", "flood is coming", "rising water"],
        "answer": (
            "During a flood: move to higher ground immediately, avoid walking "
            "or driving through moving water (even 15cm can knock you over, "
            "30cm can float a car), stay off bridges over fast-moving water, "
            "and avoid contact with floodwater — it can be electrically live "
            "or contaminated. Turn off electricity at the mains if it's safe "
            "to reach the switch without stepping in water."
        ),
    },
    "cyclone_safety": {
        "keywords": ["cyclone safety", "hurricane safety", "storm safety", "typhoon", "cyclone is coming", "cyclone warning"],
        "answer": (
            "Before a cyclone: secure or bring in loose outdoor items, board "
            "up or tape large windows, and charge all devices. During it: "
            "stay away from windows, shelter in the smallest interior room on "
            "the lowest floor, and don't go outside during a lull — the eye "
            "can pass and winds return from the opposite direction."
        ),
    },
    "landslide_safety": {
        "keywords": ["landslide safety", "landslide warning", "mudslide", "landslide is coming"],
        "answer": (
            "Warning signs of an incoming landslide: a faint rumbling sound "
            "that increases, doors or windows suddenly jamming, new cracks in "
            "walls or the ground, and trees or poles tilting. If you notice "
            "these, move away from the slope path immediately — sideways and "
            "uphill, not straight downhill in the direction debris would flow."
        ),
    },
    "earthquake_safety": {
        "keywords": ["earthquake safety", "earthquake tips", "aftershock", "earthquake warning"],
        "answer": (
            "Earthquakes aren't predicted by this system — they don't show up "
            "in weather data, so there's no live earthquake risk score here. "
            "General guidance: Drop, Cover, and Hold On — get under sturdy "
            "furniture, protect your head and neck, and stay away from "
            "windows and heavy furniture that could fall. If outdoors, move "
            "to open ground away from buildings and power lines."
        ),
    },
    "power_outage": {
        "keywords": ["power outage", "power cut", "no electricity", "blackout"],
        "answer": (
            "During a power outage: keep the fridge/freezer closed as much as "
            "possible (food stays cold for a few hours), use flashlights "
            "rather than candles to avoid fire risk, unplug sensitive "
            "electronics in case of a power surge when it comes back, and "
            "check on neighbours who rely on medical equipment."
        ),
    },
    "water_purification": {
        "keywords": ["purify water", "clean water", "water safe to drink", "boil water", "safe drinking water"],
        "answer": (
            "If tap water may be contaminated: boiling for at least 1 minute "
            "kills most pathogens (3 minutes above 2000m altitude). If you "
            "can't boil, unscented household bleach works too — about 2 drops "
            "per litre, stirred and left for 30 minutes. Avoid floodwater "
            "entirely, even boiled, if it may contain chemical runoff."
        ),
    },
    "first_aid_basics": {
        "keywords": ["first aid", "bleeding", "someone is hurt", "injured person", "cpr"],
        "answer": (
            "For a real injury, call local emergency services first. General "
            "steps while help is on the way: for bleeding, apply firm direct "
            "pressure with a clean cloth and keep the area raised if possible; "
            "for someone unconscious but breathing, place them on their side "
            "in the recovery position. I can't give medical guidance beyond "
            "this — please get them to a hospital or call for help."
        ),
    },
}


def _match_faq(q):
    """Returns the topic whose matched keyword is the longest (most
    specific) substring hit, not just the first topic in dict order.
    Without this, a question like "how do I purify water during a flood"
    would match flood_safety's "during a flood" before ever considering
    water_purification's "purify water" — whichever topic happened to be
    defined first would silently win regardless of which keyword is
    actually the better signal."""
    best_topic, best_answer, best_len = None, None, 0
    for topic_id, topic in FAQ_TOPICS.items():
        for kw in topic["keywords"]:
            if kw in q and len(kw) > best_len:
                best_topic, best_answer, best_len = topic_id, topic["answer"], len(kw)
    return best_topic, best_answer


# ------------------------------------------------------------ zone lookup --

def find_zone(text, zones):
    """Loose zone-name lookup: full name match first, then first-word match
    (so "hsr" matches "HSR Layout"). Pure function — no I/O — so it's
    tested directly in tests/test_citizen_chat.py without a live DB."""
    text_l = text.lower()
    for z in zones:
        if z["name"].lower() in text_l:
            return z
    for z in zones:
        first_word = z["name"].split()[0].lower()
        if first_word and first_word in text_l:
            return z
    return None


def _build_context():
    weather = weather_agent.assess_all_zones()
    weather_by_zone = {w["zone"]: w for w in weather}
    traffic = traffic_agent.assess_all_zones(weather_by_zone)
    hospitals = hospital_agent.get_all_hospitals()
    rescue_summary = rescue_agent.status_summary()
    return weather, traffic, hospitals, rescue_summary


HAZARD_TIP_BY_PREDICTION = {
    "Flood": "flood_safety",
    "Cyclone": "cyclone_safety",
    "Landslide": "landslide_safety",
}


def _rule_based_answer(question, zones):
    """Returns (answer_text, source_detail). source_detail is one of
    "assistant" (small talk / meta), "live_data" (pulled from the other
    agents right now), or "general_knowledge" (static safety info, not
    tied to current conditions) — kept distinct so the UI can be honest
    about which answers are backed by live conditions and which aren't."""
    q = question.lower().strip()
    zone = find_zone(question, zones)

    # --- 1. meta / small talk -----------------------------------------
    if re.search(r"\b(bye|goodbye|see you)\b", q) and len(q) < 20:
        return "Stay safe. Come back any time you want a risk or route check.", "assistant"

    if re.search(r"\b(thanks|thank you|thx)\b", q) and len(q) < 25:
        return "You're welcome — stay safe out there.", "assistant"

    if re.search(r"\b(who are you|what are you|what can you do|what do you do|help me)\b", q):
        return (
            "I'm the Nirvaha assistant. I can tell you: current risk for any "
            "monitored area, the safest evacuation route from where you are, "
            "nearby hospitals with live bed counts and phone numbers, and "
            "general safety tips (emergency kits, flood/cyclone/landslide/"
            "earthquake prep, water purification, power outages, basic first "
            "aid). Just ask in plain language."
        ), "assistant"

    if re.search(r"\b(what is nirvaha|about nirvaha|how does this (app|site|work))\b", q):
        return (
            "Nirvaha coordinates disaster response using live weather, "
            "traffic, hospital, and rescue-team data. This chat and the rest "
            "of the community pages read the same live numbers the response "
            "team's dashboard uses — nothing here is a separate, possibly "
            "outdated copy."
        ), "assistant"

    if len(q) < 20 and re.search(r"\b(hello|hi|hey)\b", q):
        return (
            "Hi! Ask me about current risk, evacuation routes, hospitals, or "
            "general safety prep. Try \"What's the risk in Koramangala?\" or "
            "\"What should I pack in an emergency kit?\""
        ), "assistant"

    # --- 2. live-data lookups (only when the question is clearly about a
    # specific place, not just incidentally containing a hazard word) -----
    evacuation_hit = bool(re.search(r"\b(evacuat|escape route|safe way out|which way|route)\b", q))
    hospital_hit = any(k in q for k in ("hospital", "medical", "doctor", "clinic", "ambulance"))
    risk_hit = any(k in q for k in ("risk", "danger", "how bad", "how safe"))
    contact_hit = any(k in q for k in ("emergency number", "helpline", "contact number", "phone number")) or \
        (("emergency" in q or "contact" in q) and "kit" not in q)

    # A bare "flood"/"cyclone"/"landslide" mention is treated as a possible
    # risk query only when paired with an actual zone — otherwise it's very
    # likely a general safety question ("how do I purify water during a
    # flood") that should reach the FAQ layer below, not get swallowed here.
    hazard_word_with_zone = zone is not None and any(
        k in q for k in ("flood", "cyclone", "landslide")
    )

    weather, traffic, hospitals, rescue_summary = _build_context()

    if zone and evacuation_hit:
        route = traffic_agent.safest_route(zone["name"], traffic)
        return (
            f"From {zone['name']}, the safest route right now leads toward "
            f"{route['to']} — about {route['distance_km']} km, roughly "
            f"{route['duration_min']} minutes by road."
        ), "live_data"
    if not zone and evacuation_hit:
        return "Tell me which area you're evacuating from — for example \"evacuation route from Koramangala\".", "assistant"

    if hospital_hit:
        if zone:
            weather_by_zone = {w["zone"]: w for w in weather}
            recs = hospital_agent.recommend_for_zone(zone, top_n=2, weather_by_zone=weather_by_zone)
            if recs:
                parts = []
                for h in recs:
                    part = (
                        f"{h['hospital_name']} ({h['distance_km']} km away, "
                        f"{h['beds_available']} beds free) — call {h['contact']}"
                    )
                    if h["accessibility_risk"] != "Low":
                        part += f" [⚠ {h['accessibility_status']}]"
                    parts.append(part)
                return "Nearest hospitals for you: " + "; ".join(parts) + ".", "live_data"
        parts = [f"{h['hospital_name']} — {h['contact']}" for h in hospitals]
        return "Hospital contacts: " + "; ".join(parts) + ".", "live_data"

    if risk_hit or hazard_word_with_zone:
        if zone:
            w = next((x for x in weather if x["zone"] == zone["name"]), None)
            if w:
                base = (
                    f"{zone['name']} is currently at {w['risk_level'].upper()} risk "
                    f"— most likely {w['prediction']} (confidence {round(w['risk_score'] * 100)}%). "
                    f"Current conditions: {w['weather']['rainfall_mm']}mm rainfall, "
                    f"{w['weather']['wind_speed_kmh']}km/h wind."
                )
                tip_topic = HAZARD_TIP_BY_PREDICTION.get(w["prediction"])
                if tip_topic and w["risk_level"] != "Low":
                    base += " " + FAQ_TOPICS[tip_topic]["answer"].split(". ")[0] + "."
                return base, "live_data"
        if risk_hit:
            worst = max(weather, key=lambda w: w["risk_score"]) if weather else None
            if worst:
                return (
                    f"The highest-risk area right now is {worst['zone']} "
                    f"({worst['risk_level']} risk, {worst['prediction']}). "
                    f"Ask about your own area by name for a specific answer."
                ), "live_data"

    if contact_hit:
        parts = [f"{h['hospital_name']}: {h['contact']}" for h in hospitals]
        return (
            "Emergency contacts — " + "; ".join(parts) + ". "
            f"Rescue teams available right now: {rescue_summary['teams_available']}/{rescue_summary['teams_total']}."
        ), "live_data"

    # --- 3. general safety knowledge ------------------------------------
    topic_id, faq_answer = _match_faq(q)
    if faq_answer:
        return faq_answer, "general_knowledge"

    # --- 4. fallback -----------------------------------------------------
    zone_names = ", ".join(z["name"] for z in zones)
    topics = ", ".join(t.replace("_", " ") for t in FAQ_TOPICS)
    return (
        "I'm not sure how to answer that yet. I can help with: current risk, "
        f"evacuation routes, and hospital contacts for {zone_names}; and "
        f"general safety topics like {topics}. Try rephrasing, or ask "
        "\"what can you do?\""
    ), "assistant"


def _claude_answer(question):
    if not Config.ANTHROPIC_API_KEY or anthropic is None:
        return None
    try:
        weather, traffic, hospitals, rescue_summary = _build_context()
        lines = []
        for w in weather:
            lines.append(f"- {w['zone']}: {w['risk_level']} risk, likely {w['prediction']} "
                          f"({w['weather']['rainfall_mm']}mm rain, {w['weather']['wind_speed_kmh']}km/h wind)")
        for t in traffic:
            lines.append(f"- {t['zone']} roads: {t['road_status']} ({t['congestion_level']} congestion)")
        for h in hospitals:
            lines.append(f"- Hospital {h['hospital_name']} in {h['location']}: "
                          f"{h['beds_available']}/{h['beds_total']} beds free, contact {h['contact']}")
        lines.append(f"- Rescue teams available: {rescue_summary['teams_available']}/{rescue_summary['teams_total']}")
        context = "\n".join(lines)

        client = anthropic.Anthropic(api_key=Config.ANTHROPIC_API_KEY)
        resp = client.messages.create(
            model=Config.ANTHROPIC_CHAT_MODEL,
            max_tokens=350,
            system=(
                "You are the Nirvaha assistant, a calm, concise public helper "
                "for a disaster-response app. Two kinds of questions:\n"
                "1. Anything specific to a zone, hospital, route, or team "
                "count — answer ONLY from the live data below. Never invent a "
                "risk level, hospital name, phone number, or distance that "
                "isn't in it. If the live data doesn't cover it, say so "
                "plainly rather than guessing.\n"
                "2. General safety/preparedness knowledge (emergency kits, "
                "flood/cyclone/landslide/earthquake prep, water purification, "
                "first aid basics, power outages) — you may answer these from "
                "your own general knowledge, since they're not live data. Keep "
                "any first-aid answer to general steps plus 'call emergency "
                "services' — never a diagnosis.\n"
                "Keep answers short and practical; this may be read by someone "
                "in a stressful situation.\n\nLive data:\n" + context
            ),
            messages=[{"role": "user", "content": question}],
        )
        text = "".join(block.text for block in resp.content if getattr(block, "type", None) == "text").strip()
        return text or None
    except Exception:
        return None  # fall back to the rule-based matcher below


def answer(question):
    """Public entry point. Always returns a usable answer — never raises,
    never returns an empty string — regardless of which mode served it.
    `source` is "claude", "live_data", "general_knowledge", or "assistant"
    so the UI can be accurate about whether an answer reflects current
    conditions or is static safety information."""
    zones = get_zones()
    if not question or not question.strip():
        return {"answer": "Ask me something like \"What's the risk in my area?\"", "source": "assistant"}

    claude_reply = _claude_answer(question)
    if claude_reply:
        return {"answer": claude_reply, "source": "claude"}
    text, source = _rule_based_answer(question, zones)
    return {"answer": text, "source": source}
