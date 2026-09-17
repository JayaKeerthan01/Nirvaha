"""
Pure scoring functions used by the Hospital and Rescue agents.

These were previously inlined inside the agent classes, which meant testing
the actual decision math required a live SQLite connection. Pulling them out
as plain functions (no DB, no I/O) makes them trivial to unit test — see
tests/test_scoring.py.
"""

RISK_WEIGHT = {"Low": 0.2, "Medium": 0.55, "High": 1.0}


def hospital_capacity_score(beds_available, beds_total, icu_available, doctors_available):
    """0..~1 score: higher = more spare capacity to take patients."""
    beds_total = max(beds_total, 1)
    return (
        (beds_available / beds_total) * 0.5
        + min(icu_available / 10, 1) * 0.3
        + min(doctors_available / 40, 1) * 0.2
    )


def hospital_suitability(capacity_score, distance_km):
    """Higher = better choice: more capacity, closer distance."""
    return capacity_score * 100 - distance_km * 1.5


# A disaster doesn't just create casualties in the affected zone — it can
# just as easily damage, cut off, or overwhelm a hospital that happens to
# sit inside a high-risk zone itself. Previously recommend_for_zone()
# scored purely on capacity + distance with zero awareness of whether the
# hospital's OWN location was safe, which meant a hospital in the middle
# of a flood zone could still come back as the top pick for a nearby
# disaster. This penalizes (rather than silently hides) that case, so a
# human operator can see the risk and still override it if it's genuinely
# the only option nearby.
HOSPITAL_ACCESSIBILITY_PENALTY = {"Low": 0, "Medium": 30, "High": 80}
HOSPITAL_ACCESSIBILITY_STATUS = {
    "Low": "Operational",
    "Medium": "Access may be delayed",
    "High": "Likely unreachable — itself in a high-risk area",
}


def hospital_accessibility_penalty(hospital_zone_risk_level):
    return HOSPITAL_ACCESSIBILITY_PENALTY.get(hospital_zone_risk_level, 0)


def hospital_accessibility_status(hospital_zone_risk_level):
    return HOSPITAL_ACCESSIBILITY_STATUS.get(hospital_zone_risk_level, "Operational")


def rescue_priority_score(risk_level, density):
    """0..1 zone priority: weighted disaster severity + population density."""
    severity_weight = RISK_WEIGHT.get(risk_level, 0.2)
    return severity_weight * 0.65 + density * 0.35
