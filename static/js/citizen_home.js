function riskWord(level) {
  return { Low: "All clear", Medium: "Stay alert", High: "Take action" }[level] || level;
}

function updateEmergencyBanner(zones) {
  const banner = document.getElementById("citizen-emergency-banner");
  if (!banner) return;

  const homeZone = window.USER_HOME_ZONE;
  const highRiskZones = zones.filter((z) => z.risk_level === "High");
  const homeZoneRisk = homeZone ? zones.find((z) => z.zone.toLowerCase() === homeZone.toLowerCase()) : null;

  if (homeZoneRisk && homeZoneRisk.risk_level === "High") {
    banner.style.display = "block";
    banner.innerHTML = `
      <div class="citizen-alert-banner banner-high">
        <div class="banner-icon-pulse">🚨</div>
        <div class="banner-body">
          <div class="banner-headline">CRITICAL DISASTER ALERT: ${homeZoneRisk.zone.toUpperCase()}</div>
          <div class="banner-sub">${homeZoneRisk.prediction} detected in your registered home area. Take immediate precautions, secure essentials, and locate your nearest safe shelter.</div>
        </div>
        <a href="/citizen/evacuation" class="btn btn-primary banner-action-btn">View Shelter Route</a>
      </div>
    `;
  } else if (highRiskZones.length > 0) {
    banner.style.display = "block";
    const zoneNames = highRiskZones.map((z) => z.zone).join(", ");
    banner.innerHTML = `
      <div class="citizen-alert-banner banner-warning">
        <div class="banner-icon-pulse">⚠</div>
        <div class="banner-body">
          <div class="banner-headline">REGIONAL SEVERE WEATHER ADVISORY</div>
          <div class="banner-sub">Severe conditions reported in: <strong>${zoneNames}</strong>. Avoid transit through affected corridors.</div>
        </div>
        <a href="/citizen/evacuation" class="btn btn-ghost banner-action-btn">Evacuation Guide</a>
      </div>
    `;
  } else {
    banner.style.display = "none";
  }
}

async function refreshCitizenRisk() {
  const container = document.getElementById("risk-list");
  try {
    const zones = await getJSON("/api/weather");
    updateEmergencyBanner(zones);

    container.innerHTML = "";
    zones
      .sort((a, b) => b.risk_score - a.risk_score)
      .forEach((w) => {
        const isHome = window.USER_HOME_ZONE && w.zone.toLowerCase() === window.USER_HOME_ZONE.toLowerCase();
        container.appendChild(
          el(`
            <div class="risk-card-lg risk-${w.risk_level.toLowerCase()} ${isHome ? 'risk-home-highlight' : ''}">
              <div>
                <div class="zone-name">${w.zone} ${isHome ? '<span class="badge badge-info" style="margin-left:6px; font-size:11px;">Your Area</span>' : ''}</div>
                <div class="zone-detail">${w.prediction} · ${w.weather.rainfall_mm}mm rain · ${w.weather.wind_speed_kmh}km/h wind · ${w.weather.temperature_c}°C</div>
              </div>
              <div class="risk-word">${riskWord(w.risk_level)}</div>
            </div>
          `)
        );
      });
  } catch (e) {
    container.innerHTML = `<div class="empty-state">Couldn't load risk data. Try refreshing the page.</div>`;
    console.error(e);
  }
}

refreshCitizenRisk();
setInterval(refreshCitizenRisk, POLL_INTERVAL_MS);
