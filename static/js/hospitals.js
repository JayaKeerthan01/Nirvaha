function bedBadge(available, total) {
  const ratio = available / Math.max(total, 1);
  if (ratio > 0.4) return "badge-low";
  if (ratio > 0.15) return "badge-medium";
  return "badge-high";
}

function accessibilityBadge(risk) {
  return { Low: "badge-low", Medium: "badge-medium", High: "badge-high" }[risk] || "badge-low";
}

function accessibilityShortLabel(risk) {
  return { Low: "Operational", Medium: "Delayed access", High: "Unreachable" }[risk] || "Operational";
}

async function renderHospitals() {
  const rows = document.getElementById("hospital-rows");
  try {
    const hospitals = await getJSON("/api/hospitals");
    rows.innerHTML = "";
    hospitals.forEach((h) => {
      rows.appendChild(
        el(`
          <tr>
            <td><b>${h.hospital_name}</b></td>
            <td>${h.location}</td>
            <td><span class="badge ${accessibilityBadge(h.accessibility_risk)}" title="${h.accessibility_status}">${accessibilityShortLabel(h.accessibility_risk)}</span></td>
            <td><span class="badge ${bedBadge(h.beds_available, h.beds_total)}">${h.beds_available} / ${h.beds_total}</span></td>
            <td class="mono">${h.icu_available}</td>
            <td class="mono">${h.doctors_available}</td>
            <td class="mono">${h.ambulances_available}</td>
            <td class="mono">${h.contact}</td>
          </tr>
        `)
      );
    });
  } catch (e) {
    console.error(e);
  }
}

renderHospitals();
setInterval(renderHospitals, POLL_INTERVAL_MS);
