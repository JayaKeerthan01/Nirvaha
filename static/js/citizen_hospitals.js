function currentZone() {
  const select = document.getElementById("zone-select");
  return select ? select.value : null;
}

async function refreshCitizenHospitals() {
  const container = document.getElementById("hospital-list");
  const zone = currentZone();
  if (!zone) return;

  try {
    const hospitals = await getJSON(`/api/hospitals?zone=${encodeURIComponent(zone)}`);
    container.innerHTML = "";
    if (!hospitals.length) {
      container.innerHTML = `<div class="empty-state">No hospital data available for this area.</div>`;
      return;
    }
    hospitals.forEach((h) => {
      const telHref = "tel:" + h.contact.replace(/[^0-9+]/g, "");
      const atRisk = h.accessibility_risk && h.accessibility_risk !== "Low";
      const warning = atRisk
        ? `<div class="hospital-meta" style="color:var(--alert-warning); margin-top:4px;">⚠ ${h.accessibility_status}</div>`
        : "";
      const distance = h.distance_km !== undefined ? `${h.distance_km} km away · ` : "";
      container.appendChild(
        el(`
          <div class="hospital-card-lg" style="${atRisk ? "border-color:rgba(255,178,56,0.4);" : ""}">
            <div>
              <div class="hospital-name">${h.hospital_name}</div>
              <div class="hospital-meta">${distance}${h.location} · ${h.beds_available}/${h.beds_total} beds free · ${h.icu_available} ICU · ${h.doctors_available} doctors on duty</div>
              ${warning}
            </div>
            <a class="call-btn" href="${telHref}">Call ${h.contact}</a>
          </div>
        `)
      );
    });
  } catch (e) {
    container.innerHTML = `<div class="empty-state">Couldn't load hospital data. Try refreshing.</div>`;
    console.error(e);
  }
}

document.getElementById("zone-select").addEventListener("change", refreshCitizenHospitals);

// If the person has a home zone from signup, default to it (already
// selected server-side via the `selected` attribute in the template) —
// otherwise this just uses whichever zone is first in the dropdown.
refreshCitizenHospitals();
setInterval(refreshCitizenHospitals, POLL_INTERVAL_MS);
