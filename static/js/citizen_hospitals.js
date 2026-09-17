async function refreshCitizenHospitals() {
  const container = document.getElementById("hospital-list");
  try {
    const hospitals = await getJSON("/api/hospitals");
    container.innerHTML = "";
    hospitals.forEach((h) => {
      const telHref = "tel:" + h.contact.replace(/[^0-9+]/g, "");
      const atRisk = h.accessibility_risk && h.accessibility_risk !== "Low";
      const warning = atRisk
        ? `<div class="hospital-meta" style="color:var(--alert-warning); margin-top:4px;">⚠ ${h.accessibility_status}</div>`
        : "";
      container.appendChild(
        el(`
          <div class="hospital-card-lg" style="${atRisk ? "border-color:rgba(255,178,56,0.4);" : ""}">
            <div>
              <div class="hospital-name">${h.hospital_name}</div>
              <div class="hospital-meta">${h.location} · ${h.beds_available}/${h.beds_total} beds free · ${h.icu_available} ICU · ${h.doctors_available} doctors on duty</div>
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

refreshCitizenHospitals();
setInterval(refreshCitizenHospitals, POLL_INTERVAL_MS);
