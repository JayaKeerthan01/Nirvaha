let allZonesData = [];
let currentFilter = 'all';

function riskWord(level) {
  return { Low: "All clear", Medium: "Stay alert", High: "Take action" }[level] || level;
}

function riskColor(level) {
  return { Low: "#15803d", Medium: "#b45309", High: "#b91c1c" }[level] || "#0284c7";
}

function riskBadgeStyle(level) {
  if (level === 'High') {
    return 'color:#b91c1c; background:#fef2f2; border:1px solid #fecaca;';
  } else if (level === 'Medium') {
    return 'color:#b45309; background:#fffbeb; border:1px solid #fde68a;';
  } else {
    return 'color:#15803d; background:#f0fdf4; border:1px solid #bbf7d0;';
  }
}

function setRiskFilter(filter) {
  currentFilter = filter;
  document.querySelectorAll('.radar-tab').forEach(tab => tab.classList.remove('active'));
  const activeTab = document.getElementById('tab-' + filter);
  if (activeTab) activeTab.classList.add('active');
  renderRiskList(allZonesData);
}

function updateEmergencyBanner(zones) {
  const banner = document.getElementById("citizen-emergency-banner");
  const statusPill = document.getElementById("citizen-network-status");
  if (!banner) return;

  const homeZone = window.USER_HOME_ZONE;
  const highRiskZones = zones.filter((z) => z.risk_level === "High");
  const homeZoneRisk = homeZone ? zones.find((z) => z.zone.toLowerCase() === homeZone.toLowerCase()) : null;

  if (homeZoneRisk && homeZoneRisk.risk_level === "High") {
    if (statusPill) statusPill.textContent = "High Advisory in Your Area";
    banner.style.display = "block";
    banner.innerHTML = `
      <div class="citizen-alert-banner banner-high">
        <div style="font-size:32px; line-height:1;">⚠️</div>
        <div class="banner-body" style="flex:1;">
          <div class="banner-headline" style="font-size:16px; font-weight:700; color:#991b1b; margin-bottom:4px;">
            SEVERE WEATHER ADVISORY: ${homeZoneRisk.zone.toUpperCase()}
          </div>
          <div class="banner-sub" style="font-size:13.5px; color:#7f1d1d; line-height:1.4;">
            ${homeZoneRisk.prediction} detected in your registered neighborhood. Avoid unnecessary road transit, stay on upper floors if waterlogging occurs, and know your nearest relief camp.
          </div>
        </div>
        <a href="/citizen/evacuation" class="btn btn-primary" style="white-space:nowrap; padding:10px 18px; border-radius:10px; background:#dc2626; color:#ffffff; border:none; font-weight:700; text-decoration:none;">
          Nearest Shelter Route ➔
        </a>
      </div>
    `;
  } else if (highRiskZones.length > 0) {
    if (statusPill) statusPill.textContent = "Regional Weather Advisory";
    banner.style.display = "block";
    const zoneNames = highRiskZones.map((z) => z.zone).join(", ");
    banner.innerHTML = `
      <div class="citizen-alert-banner banner-warning">
        <div style="font-size:28px; line-height:1;">📢</div>
        <div class="banner-body" style="flex:1;">
          <div class="banner-headline" style="font-size:15px; font-weight:700; color:#92400e; margin-bottom:4px;">
            REGIONAL ADVISORY: ACTIVE IN ${highRiskZones.length} DISTRICT(S)
          </div>
          <div class="banner-sub" style="font-size:13px; color:#78350f; line-height:1.4;">
            Severe rain & storm conditions detected in: <strong>${zoneNames}</strong>. Neighboring transport corridors may face severe delays or temporary closures.
          </div>
        </div>
        <a href="/citizen/evacuation" class="btn" style="white-space:nowrap; padding:8px 16px; border-radius:10px; border:1px solid #d97706; background:#ffffff; color:#b45309; font-weight:600; text-decoration:none;">
          View Safe Routes ➔
        </a>
      </div>
    `;
  } else {
    if (statusPill) statusPill.textContent = "Civic Alert Grid Active • Normal";
    banner.style.display = "none";
  }
}

function renderRiskList(zones) {
  const container = document.getElementById("risk-list");
  if (!container) return;

  let filtered = [...zones];
  const homeZone = window.USER_HOME_ZONE;

  if (currentFilter === 'home') {
    filtered = filtered.filter(z => homeZone && z.zone.toLowerCase() === homeZone.toLowerCase());
  } else if (currentFilter === 'alert') {
    filtered = filtered.filter(z => z.risk_level === 'High' || z.risk_level === 'Medium');
  }

  if (filtered.length === 0) {
    container.innerHTML = `
      <div style="padding: 24px; text-align: center; color: #64748b; font-size: 14px;">
        ${currentFilter === 'alert' ? '✨ No active alerts right now! All districts report safe conditions.' : 'No districts found.'}
      </div>
    `;
    return;
  }

  container.innerHTML = "";
  filtered
    .sort((a, b) => b.risk_score - a.risk_score)
    .forEach((w) => {
      const isHome = homeZone && w.zone.toLowerCase() === homeZone.toLowerCase();
      const statusWord = riskWord(w.risk_level);
      const color = riskColor(w.risk_level);
      const badgeStyle = riskBadgeStyle(w.risk_level);

      const item = document.createElement("div");
      item.className = `citizen-risk-item risk-${w.risk_level.toLowerCase()} ${isHome ? 'is-home-zone' : ''}`;
      item.innerHTML = `
        <div style="flex:1; min-width:200px;">
          <div style="display:flex; align-items:center; gap:8px; margin-bottom:4px;">
            <span style="font-family:'Outfit', sans-serif; font-size:16px; font-weight:700; color:#0f172a;">
              ${w.zone}
            </span>
            ${isHome ? '<span style="font-size:11px; font-weight:700; color:#0369a1; background:#e0f2fe; border:1px solid #bae6fd; padding:2px 8px; border-radius:12px;">Your Home Area</span>' : ''}
          </div>
          <div style="font-size:13.5px; color:#475569; margin-bottom:6px;">
            ${w.prediction}
          </div>
          <div style="display:flex; align-items:center; gap:10px; flex-wrap:wrap; font-size:12px; color:#64748b;">
            <span>🌧️ <strong>${w.weather.rainfall_mm} mm</strong> rain</span>
            <span>•</span>
            <span>💨 <strong>${w.weather.wind_speed_kmh} km/h</strong> wind</span>
            <span>•</span>
            <span>🌡️ <strong>${w.weather.temperature_c}°C</strong></span>
          </div>
        </div>
        <div style="text-align:right; display:flex; flex-direction:column; align-items:flex-end; gap:6px;">
          <span style="display:inline-flex; align-items:center; gap:6px; font-family:'Outfit', sans-serif; font-size:12.5px; font-weight:700; ${badgeStyle} padding:4px 12px; border-radius:9999px;">
            <span style="width:7px; height:7px; border-radius:50%; background:${color};"></span>
            ${statusWord}
          </span>
          ${w.risk_level === 'High' ? '<a href="/citizen/evacuation" style="font-size:12px; color:#dc2626; font-weight:600; text-decoration:none;">Shelter Guide ➔</a>' : ''}
        </div>
      `;
      container.appendChild(item);
    });
}

async function refreshCitizenRisk() {
  const container = document.getElementById("risk-list");
  const timeLabel = document.getElementById("lastRefreshedTime");
  try {
    const zones = await getJSON("/api/weather");
    allZonesData = zones;
    updateEmergencyBanner(zones);
    renderRiskList(zones);

    if (timeLabel) {
      const now = new Date();
      timeLabel.textContent = `Updated ${now.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })}`;
    }
  } catch (e) {
    if (container && container.innerHTML.trim() === "") {
      container.innerHTML = `<div style="padding:20px; text-align:center; color:#64748b;">Couldn't load risk data. Please try refreshing.</div>`;
    }
    console.error("Telemetry refresh error:", e);
  }
}

async function manualRefreshRisk() {
  const icon = document.getElementById("refreshIcon");
  if (icon) {
    icon.style.transform = "rotate(360deg)";
  }
  await refreshCitizenRisk();
  setTimeout(() => {
    if (icon) icon.style.transform = "none";
  }, 600);
}

/* ==========================================================================
   Interactive Household Preparedness Checklist Logic (Saved to LocalStorage)
   ========================================================================== */
const CHECKLIST_STORAGE_KEY = "nirvaha_citizen_checklist_v1";

function loadChecklistState() {
  try {
    const saved = localStorage.getItem(CHECKLIST_STORAGE_KEY);
    return saved ? JSON.parse(saved) : {};
  } catch (e) {
    return {};
  }
}

function saveChecklistState(state) {
  try {
    localStorage.setItem(CHECKLIST_STORAGE_KEY, JSON.stringify(state));
  } catch (e) {
    console.error("Failed to save checklist state:", e);
  }
}

function updateChecklistProgress() {
  const state = loadChecklistState();
  let count = 0;
  const total = 5;

  for (let i = 1; i <= total; i++) {
    const isChecked = !!state[i];
    const checkbox = document.getElementById(`chk-box-${i}`);
    const item = document.getElementById(`chk-item-${i}`);

    if (checkbox) checkbox.checked = isChecked;
    if (item) {
      if (isChecked) {
        item.classList.add("checked");
      } else {
        item.classList.remove("checked");
      }
    }
    if (isChecked) count++;
  }

  const pct = Math.round((count / total) * 100);
  const fill = document.getElementById("checklist-progress-fill");
  const text = document.getElementById("checklist-progress-text");

  if (fill) fill.style.width = `${pct}%`;
  if (text) {
    if (count === total) {
      text.textContent = `🎉 5 / 5 Fully Ready!`;
      text.style.color = "#15803d";
    } else {
      text.textContent = `${count} / ${total} Ready (${pct}%)`;
      text.style.color = count >= 3 ? "#0369a1" : "#b45309";
    }
  }
}

function toggleChecklistItem(id) {
  const state = loadChecklistState();
  const checkbox = document.getElementById(`chk-box-${id}`);
  if (checkbox) {
    state[id] = checkbox.checked;
    saveChecklistState(state);
    updateChecklistProgress();
  }
}

// Initial Kick-off
document.addEventListener("DOMContentLoaded", () => {
  updateChecklistProgress();
});

refreshCitizenRisk();
setInterval(refreshCitizenRisk, POLL_INTERVAL_MS);
