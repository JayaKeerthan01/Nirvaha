/**
 * Nirvaha Unified Command Dashboard & Map Engine
 * Handles:
 *  - Leaflet / Multi-layer Command Map (Zones, Hospitals, Rescue Teams, Alerts, Auto-Routes)
 *  - Real-time Push Notifications (SSE Stream, Web Notifications API, Web Audio Chime)
 *  - Hospital & Rescue Auto-Assignment Matrix & Batch Dispatch
 *  - Advanced Gauges: Hospital Capacity, ICU Availability, Fleet Readiness
 *  - Live Emergency Bulletin Ticker & Staff Broadcast Modal
 */

// Map & Layer State
let map;
let mapReady = false;
let currentBasemap = "dark";
let tileLayers = {};
let layerGroups = {
  zones: null,
  heatmap: null,
  satellite_radar: null,
  iot_sensors: null,
  social_distress: null,
  hospitals: null,
  teams: null,
  alerts: null,
  routes: null,
};
let activeLayers = {
  zones: true,
  heatmap: false,
  satellite_radar: false,
  iot_sensors: true,
  social_distress: false,
  hospitals: true,
  teams: true,
  alerts: true,
  routes: true,
};

// Markers & Route Caches
let zoneMarkers = {};
let hospitalMarkers = {};
let teamMarkers = {};
let alertMarkers = {};
let iotMarkers = {};
let socialMarkers = {};
let radarTileLayer = null;
let routePolylines = [];
let boundsFitted = false;
let currentSocialPlatform = "all";
let latestDashboardData = null;

// Audio & Push Notification State
let audioAlertsEnabled = localStorage.getItem("nirvaha_audio_alerts") !== "false";
let audioCtx = null;
let lastAlertIds = new Set();
let currentAutoDispatchActive = false;

// -------------------------------------------------------- Audio & Push Helpers ----

function initAudio() {
  if (!audioCtx) {
    const AudioContext = window.AudioContext || window.webkitAudioContext;
    if (AudioContext) audioCtx = new AudioContext();
  }
}

function playEmergencyChime() {
  if (!audioAlertsEnabled) return;
  try {
    initAudio();
    if (!audioCtx) return;
    if (audioCtx.state === "suspended") audioCtx.resume();

    const now = audioCtx.currentTime;
    // Dual-tone attention chime
    const osc1 = audioCtx.createOscillator();
    const osc2 = audioCtx.createOscillator();
    const gain = audioCtx.createGain();

    osc1.type = "sine";
    osc2.type = "sine";

    osc1.frequency.setValueAtTime(880, now);        // A5
    osc1.frequency.setValueAtTime(1174.66, now + 0.15); // D6
    osc2.frequency.setValueAtTime(440, now);
    osc2.frequency.setValueAtTime(587.33, now + 0.15);

    gain.gain.setValueAtTime(0.3, now);
    gain.gain.exponentialRampToValueAtTime(0.001, now + 0.45);

    osc1.connect(gain);
    osc2.connect(gain);
    gain.connect(audioCtx.destination);

    osc1.start(now);
    osc2.start(now);
    osc1.stop(now + 0.5);
    osc2.stop(now + 0.5);
  } catch (e) {
    console.warn("Audio playback not permitted or unavailable:", e);
  }
}

function updateAudioButtonUI() {
  const icon = document.getElementById("sound-icon");
  const btn = document.getElementById("sound-toggle-btn");
  if (!icon || !btn) return;
  if (audioAlertsEnabled) {
    icon.textContent = "🔊";
    btn.style.opacity = "1";
    btn.title = "Audio Alerts: Enabled (Click to Mute)";
  } else {
    icon.textContent = "🔇";
    btn.style.opacity = "0.5";
    btn.title = "Audio Alerts: Muted (Click to Enable)";
  }
}

function initAudioControls() {
  const btn = document.getElementById("sound-toggle-btn");
  if (btn) {
    btn.addEventListener("click", () => {
      initAudio();
      audioAlertsEnabled = !audioAlertsEnabled;
      localStorage.setItem("nirvaha_audio_alerts", audioAlertsEnabled);
      updateAudioButtonUI();
      if (audioAlertsEnabled) playEmergencyChime();
    });
    updateAudioButtonUI();
  }
}

function initPushControls() {
  const pushBtn = document.getElementById("push-perm-btn");
  if (!pushBtn) return;

  function updatePushUI() {
    if (!("Notification" in window)) {
      pushBtn.style.display = "none";
      return;
    }
    if (Notification.permission === "granted") {
      pushBtn.title = "Desktop Push Notifications: Active";
      pushBtn.style.opacity = "1";
      pushBtn.style.color = "var(--alert-nominal)";
    } else if (Notification.permission === "denied") {
      pushBtn.title = "Desktop Push: Blocked in Browser Settings";
      pushBtn.style.opacity = "0.4";
    } else {
      pushBtn.title = "Click to Enable Desktop Push Notifications";
      pushBtn.style.opacity = "0.7";
    }
  }

  pushBtn.addEventListener("click", async () => {
    if ("Notification" in window) {
      const perm = await Notification.requestPermission();
      updatePushUI();
      if (perm === "granted") {
        new Notification("Nirvaha Disaster Response", {
          body: "Push notification alert channel successfully enabled.",
          icon: "/static/img/brand_mark.png",
        });
      }
    }
  });
  updatePushUI();
}

function showToast(title, message, severity = "info", duration = 8000) {
  const container = document.getElementById("toast-container");
  if (!container) return;

  const icon = severity === "High" ? "🚨" : severity === "Medium" ? "⚠" : "ℹ";
  const toast = el(`
    <div class="toast-item toast-${(severity || "info").toLowerCase()}">
      <div class="toast-icon">${icon}</div>
      <div class="toast-body">
        <div class="toast-title">${title}</div>
        <div class="toast-msg">${message}</div>
      </div>
      <button class="toast-close">&times;</button>
    </div>
  `);

  toast.querySelector(".toast-close").addEventListener("click", () => {
    toast.classList.add("toast-hiding");
    setTimeout(() => toast.remove(), 250);
  });

  container.appendChild(toast);

  if (duration > 0) {
    setTimeout(() => {
      if (toast.parentElement) {
        toast.classList.add("toast-hiding");
        setTimeout(() => toast.remove(), 250);
      }
    }, duration);
  }
}

// ------------------------------------------------------------- Map Engine ----

function initMap() {
  try {
    if (typeof L === "undefined") throw new Error("Leaflet library not loaded");

    map = L.map("map", {
      zoomControl: true,
      attributionControl: false,
      zoomAnimation: true,
      fadeAnimation: true,
      markerZoomAnimation: true,
    }).setView([12.9121, 77.6446], 12);

    // Three switchable basemaps
    tileLayers.dark = L.tileLayer("https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png", { maxZoom: 19 });
    tileLayers.osm = L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", { maxZoom: 19 });
    tileLayers.satellite = L.tileLayer("https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}", { maxZoom: 18 });

    tileLayers.dark.addTo(map);

    // Initialize entity layers
    layerGroups.zones = L.layerGroup().addTo(map);
    layerGroups.heatmap = L.layerGroup();
    layerGroups.satellite_radar = L.layerGroup();
    layerGroups.iot_sensors = L.layerGroup().addTo(map);
    layerGroups.social_distress = L.layerGroup();
    layerGroups.hospitals = L.layerGroup().addTo(map);
    layerGroups.teams = L.layerGroup().addTo(map);
    layerGroups.alerts = L.layerGroup().addTo(map);
    layerGroups.routes = L.layerGroup().addTo(map);

    mapReady = true;
    initMapControls();
  } catch (e) {
    console.warn("Map initialization failed:", e);
    const el = document.getElementById("map");
    if (el) el.innerHTML = '<div class="empty-state">Map canvas offline. Please check internet connection.</div>';
  }
}

function initMapControls() {
  // Layer Filter Pills
  document.querySelectorAll(".layer-pill").forEach((btn) => {
    btn.addEventListener("click", () => {
      const layerKey = btn.dataset.layer;
      if (layerKey === "all") {
        const anyActive = Object.values(activeLayers).some((v) => v);
        const newState = !anyActive;
        Object.keys(activeLayers).forEach((k) => {
          activeLayers[k] = newState;
          if (layerGroups[k]) {
            if (newState) map.addLayer(layerGroups[k]);
            else map.removeLayer(layerGroups[k]);
          }
        });
        document.querySelectorAll(".layer-pill").forEach((p) => p.classList.toggle("active", newState));
      } else {
        activeLayers[layerKey] = !activeLayers[layerKey];
        btn.classList.toggle("active", activeLayers[layerKey]);
        if (layerGroups[layerKey]) {
          if (activeLayers[layerKey]) map.addLayer(layerGroups[layerKey]);
          else map.removeLayer(layerGroups[layerKey]);
        }
      }
    });
  });

  // Basemap Switcher Pills
  document.querySelectorAll(".basemap-pill").forEach((btn) => {
    btn.addEventListener("click", () => {
      const target = btn.dataset.basemap;
      if (target === currentBasemap) return;
      document.querySelectorAll(".basemap-pill").forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");

      if (tileLayers[currentBasemap]) map.removeLayer(tileLayers[currentBasemap]);
      if (tileLayers[target]) tileLayers[target].addTo(map);
      currentBasemap = target;
    });
  });

  // Recenter Map Button
  const recenterBtn = document.getElementById("recenter-map-btn");
  if (recenterBtn) {
    recenterBtn.addEventListener("click", () => {
      const coords = Object.values(zoneMarkers).map((m) => m.getLatLng());
      if (coords.length && map) {
        map.flyToBounds(L.latLngBounds(coords), { padding: [50, 50], maxZoom: 14, duration: 0.8 });
      }
    });
  }
}

function riskColor(level) {
  return { Low: "#2DD4BF", Medium: "#FFB238", High: "#FF5470" }[level] || "#5EA8FF";
}

function riskRadius(level) {
  return { Low: 12, Medium: 18, High: 26 }[level] || 12;
}

// ------------------------------------------------------------- Map Rendering ----

function renderUnifiedMap(data) {
  if (!mapReady) return;

  const seenZones = new Set();
  const zoneCoords = [];

  // 1. ZONES LAYER
  (data.weather || []).forEach((w) => {
    seenZones.add(w.zone);
    zoneCoords.push([w.lat, w.lon]);

    const popupHtml = `
      <div class="map-popup-card">
        <div class="popup-title">${w.zone}</div>
        <div class="popup-badge badge ${riskClass(w.risk_level)}">${w.risk_level} RISK (${Math.round(w.risk_score * 100)}%)</div>
        <div class="popup-meta"><b>Hazard:</b> ${w.prediction}</div>
        <div class="popup-meta"><b>Rainfall:</b> ${w.weather.rainfall_mm} mm &bull; <b>Wind:</b> ${w.weather.wind_speed_kmh} km/h</div>
        <div class="popup-meta"><b>Temp:</b> ${w.weather.temperature_c}&deg;C &bull; <b>Humidity:</b> ${w.weather.humidity_pct}%</div>
        <div class="popup-actions" style="margin-top:8px;">
          <button class="btn btn-primary" onclick="quickDispatchZone('${w.zone}')" style="padding:4px 8px; font-size:11px;">Auto-Assign Unit</button>
        </div>
      </div>
    `;

    let marker = zoneMarkers[w.zone];
    if (!marker) {
      marker = L.circleMarker([w.lat, w.lon], {
        radius: riskRadius(w.risk_level),
        color: riskColor(w.risk_level),
        fillColor: riskColor(w.risk_level),
        fillOpacity: 0.35,
        weight: 2,
      }).bindPopup(popupHtml).addTo(layerGroups.zones);
      zoneMarkers[w.zone] = marker;
    } else {
      marker.setStyle({
        radius: riskRadius(w.risk_level),
        color: riskColor(w.risk_level),
        fillColor: riskColor(w.risk_level),
      });
      marker.setLatLng([w.lat, w.lon]);
      marker.setPopupContent(popupHtml);
    }
    const elNode = marker.getElement && marker.getElement();
    if (elNode) elNode.classList.toggle("risk-pulse-high", w.risk_level === "High");
  });

  // Clean stale zones
  Object.keys(zoneMarkers).forEach((name) => {
    if (!seenZones.has(name)) {
      layerGroups.zones.removeLayer(zoneMarkers[name]);
      delete zoneMarkers[name];
    }
  });

  // 1b. RISK HEATMAP LAYER
  if (layerGroups.heatmap) {
    layerGroups.heatmap.clearLayers();
    (data.weather || []).forEach((w) => {
      const radiusMeters = 800 + Math.round((w.risk_score || 0.2) * 3500);
      const color = riskColor(w.risk_level);
      L.circle([w.lat, w.lon], {
        radius: radiusMeters,
        stroke: false,
        fillColor: color,
        fillOpacity: Math.min(0.55, 0.18 + ((w.risk_score || 0) * 0.35)),
      }).addTo(layerGroups.heatmap);
    });
  }

  // 1c. SATELLITE & DOPPLER RADAR OVERLAY
  if (layerGroups.satellite_radar) {
    const sat = data.satellite;
    if (sat && sat.radar_tile_url) {
      if (!radarTileLayer || radarTileLayer._radarUrl !== sat.radar_tile_url) {
        if (radarTileLayer) layerGroups.satellite_radar.removeLayer(radarTileLayer);
        radarTileLayer = L.tileLayer(sat.radar_tile_url, {
          opacity: 0.65,
          maxZoom: 18,
          zIndex: 50,
        });
        radarTileLayer._radarUrl = sat.radar_tile_url;
        layerGroups.satellite_radar.addLayer(radarTileLayer);
      }
    }
  }

  // 1d. IOT ENVIRONMENTAL SENSORS LAYER
  const seenIoT = new Set();
  (data.iot_sensors || []).forEach((s) => {
    seenIoT.add(s.id);
    const isCrit = s.status === "Critical";
    const isWarn = s.status === "Warning";
    const statusColor = isCrit ? "#FF5470" : (isWarn ? "#FFB238" : "#2DD4BF");
    const badgeClass = isCrit ? "badge-high" : (isWarn ? "badge-medium" : "badge-available");

    const popupHtml = `
      <div class="map-popup-card">
        <div class="popup-title">📡 ${s.sensor_name}</div>
        <div class="popup-badge ${badgeClass}">${s.status.toUpperCase()} (${s.fill_percentage}%)</div>
        <div class="popup-meta"><b>Type:</b> ${s.sensor_type} &bull; <b>Zone:</b> ${s.zone}</div>
        <div class="popup-meta"><b>Depth / Reading:</b> <span class="mono" style="font-weight:700; color:${statusColor};">${s.current_value} ${s.unit}</span></div>
        <div class="popup-meta"><b>Thresholds:</b> Warn &ge; ${s.warning_threshold} ${s.unit} &bull; Crit &ge; ${s.critical_threshold} ${s.unit}</div>
        <div class="popup-meta"><b>Hardware ID:</b> ${s.sensor_id} &bull; 🔋 ${s.battery_pct}%</div>
        <div class="popup-meta" style="color:var(--fog-400); margin-top:4px;">Updated: ${fmtTime(s.last_reading_time)}</div>
      </div>
    `;

    let marker = iotMarkers[s.id];
    if (!marker) {
      const icon = L.divIcon({
        className: "custom-map-pin iot-pin",
        html: `<div class="pin-badge pin-iot ${isCrit ? 'pin-crit' : ''}" title="${s.sensor_name}">📡<span class="pin-count">${s.current_value}</span></div>`,
        iconSize: [28, 28],
        iconAnchor: [14, 14],
      });
      marker = L.marker([s.lat, s.lon], { icon }).bindPopup(popupHtml).addTo(layerGroups.iot_sensors);
      iotMarkers[s.id] = marker;
    } else {
      marker.setLatLng([s.lat, s.lon]);
      marker.setPopupContent(popupHtml);
    }
  });

  Object.keys(iotMarkers).forEach((id) => {
    if (!seenIoT.has(Number(id))) {
      layerGroups.iot_sensors.removeLayer(iotMarkers[id]);
      delete iotMarkers[id];
    }
  });

  // 1e. SOCIAL MEDIA SOS DISTRESS LAYER
  const seenSocial = new Set();
  const zoneCoordsMap = {};
  (data.weather || []).forEach((w) => {
    zoneCoordsMap[w.zone] = [w.lat, w.lon];
  });

  (data.social_distress || []).forEach((p, idx) => {
    seenSocial.add(p.id);
    const center = zoneCoordsMap[p.zone];
    if (center) {
      const jitterLat = center[0] + ((idx % 5) - 2) * 0.0035;
      const jitterLon = center[1] + (((idx * 3) % 5) - 2) * 0.0035;
      const isCrit = p.urgency_level === "Critical";
      const isHigh = p.urgency_level === "High";
      const badgeClass = isCrit ? "badge-high" : (isHigh ? "badge-medium" : "badge-info");
      
      const platNorm = (p.platform || "Twitter").toLowerCase();
      const platIcon = platNorm === "instagram" ? "📸" : (platNorm === "facebook" ? "📘" : "𝕏");
      const pinClass = platNorm === "instagram" ? "pin-instagram" : (platNorm === "facebook" ? "pin-facebook" : "pin-twitter");
      const verifiedTag = p.verified ? " ✓ (Official Agency)" : "";
      const sourceLink = p.post_url ? `<div style="margin-top:6px;"><a href="${p.post_url}" target="_blank" rel="noopener noreferrer" style="color:#5EA8FF; font-size:11px; text-decoration:none;">🔗 View Original Post ↗</a></div>` : "";

      const popupHtml = `
        <div class="map-popup-card">
          <div class="popup-title">${platIcon} ${p.platform}: ${p.username}${verifiedTag}</div>
          <div class="popup-badge ${badgeClass}">${p.urgency_level.toUpperCase()} DISTRESS</div>
          <div class="popup-meta"><b>Target Zone:</b> ${p.zone}</div>
          <div class="popup-meta" style="margin-top:4px; font-style:italic; font-size:11.5px;">"${p.message}"</div>
          ${sourceLink}
          <div class="popup-meta" style="color:var(--fog-400); margin-top:4px;">Logged: ${fmtTime(p.created_at)}</div>
        </div>
      `;

      let marker = socialMarkers[p.id];
      if (!marker) {
        const icon = L.divIcon({
          className: "custom-map-pin social-pin " + pinClass,
          html: `<div class="pin-badge pin-social ${pinClass} ${isCrit ? 'risk-pulse-high' : ''}" title="${p.platform}: ${p.username}">${platIcon}</div>`,
          iconSize: [26, 26],
          iconAnchor: [13, 13],
        });
        marker = L.marker([jitterLat, jitterLon], { icon }).bindPopup(popupHtml).addTo(layerGroups.social_distress);
        socialMarkers[p.id] = marker;
      } else {
        marker.setPopupContent(popupHtml);
      }
    }
  });

  Object.keys(socialMarkers).forEach((id) => {
    if (!seenSocial.has(Number(id))) {
      layerGroups.social_distress.removeLayer(socialMarkers[id]);
      delete socialMarkers[id];
    }
  });

  // 2. HOSPITALS LAYER (All hospitals network-wide)
  const seenHospitals = new Set();
  (data.all_hospitals || []).forEach((h) => {
    seenHospitals.add(h.id);
    const atRisk = h.accessibility_risk && h.accessibility_risk !== "Low";
    const popupHtml = `
      <div class="map-popup-card">
        <div class="popup-title">🏥 ${h.hospital_name}</div>
        <div class="popup-badge ${atRisk ? 'badge-medium' : 'badge-available'}">${h.accessibility_status || "Operational"}</div>
        <div class="popup-meta"><b>District:</b> ${h.location}</div>
        <div class="popup-meta"><b>Beds Available:</b> ${h.beds_available} / ${h.beds_total}</div>
        <div class="popup-meta"><b>ICU Beds:</b> ${h.icu_available} &bull; <b>Doctors:</b> ${h.doctors_available}</div>
        <div class="popup-meta"><b>Ambulances:</b> ${h.ambulances_available} &bull; <b>Contact:</b> ${h.contact || "108"}</div>
      </div>
    `;

    let marker = hospitalMarkers[h.id];
    if (!marker) {
      const icon = L.divIcon({
        className: "custom-map-pin hospital-pin",
        html: `<div class="pin-badge pin-hosp" title="${h.hospital_name}">🏥<span class="pin-count">${h.beds_available}</span></div>`,
        iconSize: [28, 28],
        iconAnchor: [14, 14],
      });
      marker = L.marker([h.lat, h.lon], { icon }).bindPopup(popupHtml).addTo(layerGroups.hospitals);
      hospitalMarkers[h.id] = marker;
    } else {
      marker.setLatLng([h.lat, h.lon]);
      marker.setPopupContent(popupHtml);
    }
  });

  Object.keys(hospitalMarkers).forEach((id) => {
    if (!seenHospitals.has(Number(id))) {
      layerGroups.hospitals.removeLayer(hospitalMarkers[id]);
      delete hospitalMarkers[id];
    }
  });

  // 3. RESCUE TEAMS LAYER (All units)
  const seenTeams = new Set();
  (data.all_rescue_teams || []).forEach((t) => {
    seenTeams.add(t.id);
    const isDeployed = t.status === "deployed";
    const popupHtml = `
      <div class="map-popup-card">
        <div class="popup-title">🚒 ${t.team_name}</div>
        <div class="popup-badge ${isDeployed ? 'badge-medium' : 'badge-available'}">${isDeployed ? 'DEPLOYED IN FIELD' : 'AVAILABLE'}</div>
        <div class="popup-meta"><b>Stationed:</b> ${t.zone}</div>
        <div class="popup-meta"><b>Equipment:</b> ${t.vehicles} vehicles &bull; ${t.ambulances} ambulances &bull; ${t.fire_units} fire units</div>
        <div class="popup-meta"><b>Response Personnel:</b> ${t.personnel} specialists</div>
      </div>
    `;

    let marker = teamMarkers[t.id];
    if (!marker) {
      const icon = L.divIcon({
        className: "custom-map-pin team-pin",
        html: `<div class="pin-badge pin-team ${isDeployed ? 'pin-deployed' : 'pin-ready'}" title="${t.team_name}">🚒</div>`,
        iconSize: [28, 28],
        iconAnchor: [14, 14],
      });
      marker = L.marker([t.lat, t.lon], { icon }).bindPopup(popupHtml).addTo(layerGroups.teams);
      teamMarkers[t.id] = marker;
    } else {
      marker.setLatLng([t.lat, t.lon]);
      marker.setPopupContent(popupHtml);
    }
  });

  Object.keys(teamMarkers).forEach((id) => {
    if (!seenTeams.has(Number(id))) {
      layerGroups.teams.removeLayer(teamMarkers[id]);
      delete teamMarkers[id];
    }
  });

  // 4. ACTIVE ALERTS LAYER (Beacons)
  const seenAlerts = new Set();
  (data.active_alerts || []).forEach((a) => {
    seenAlerts.add(a.id);
    const popupHtml = `
      <div class="map-popup-card">
        <div class="popup-title">⚠ ${a.title}</div>
        <div class="popup-badge ${riskClass(a.severity)}">${a.severity} SEVERITY</div>
        <div class="popup-meta">${a.message}</div>
        <div class="popup-meta" style="color:var(--fog-400); margin-top:4px;">Logged: ${fmtTime(a.created_at)}</div>
      </div>
    `;

    let marker = alertMarkers[a.id];
    if (!marker && a.lat && a.lon) {
      const icon = L.divIcon({
        className: "custom-map-pin alert-beacon",
        html: `<div class="pin-beacon-pulse"></div><div class="pin-beacon-core">⚠</div>`,
        iconSize: [32, 32],
        iconAnchor: [16, 16],
      });
      marker = L.marker([a.lat, a.lon], { icon }).bindPopup(popupHtml).addTo(layerGroups.alerts);
      alertMarkers[a.id] = marker;
    }
  });

  Object.keys(alertMarkers).forEach((id) => {
    if (!seenAlerts.has(Number(id))) {
      layerGroups.alerts.removeLayer(alertMarkers[id]);
      delete alertMarkers[id];
    }
  });

  // 5. AUTO-ROUTES LAYER (Hospital routes & Rescue routes)
  layerGroups.routes.clearLayers();
  const assignments = data.zone_assignments || {};

  Object.values(assignments).forEach((assign) => {
    // Route from Zone to Assigned Hospital
    if (assign.hospital && assign.hospital.path && assign.hospital.path.length) {
      const isUrgent = assign.risk_level === "High";
      const hospPoly = L.polyline(assign.hospital.path, {
        color: isUrgent ? "#FF5470" : "#5EA8FF",
        weight: isUrgent ? 3.5 : 2.5,
        dashArray: "6 6",
        opacity: 0.85,
      }).addTo(layerGroups.routes);
      hospPoly.bindTooltip(
        `${assign.zone} &rarr; ${assign.hospital.hospital_name} (${assign.hospital.duration_min} min)`,
        { sticky: true, className: "route-tooltip" }
      );
    }

    // Route from Nearest Rescue Unit to Zone
    if (assign.rescue_route && assign.rescue_route.path && assign.rescue_route.path.length) {
      const rescuePoly = L.polyline(assign.rescue_route.path, {
        color: "#FFB238",
        weight: 3,
        dashArray: "4 8",
        opacity: 0.9,
      }).addTo(layerGroups.routes);
      rescuePoly.bindTooltip(
        `Dispatch: ${assign.rescue_route.from_team} &rarr; ${assign.zone} (${assign.rescue_route.duration_min} min)`,
        { sticky: true, className: "route-tooltip" }
      );
    }
  });

  // Initial bounds fitting
  if (!boundsFitted && zoneCoords.length > 0) {
    map.flyToBounds(L.latLngBounds(zoneCoords), { padding: [50, 50], maxZoom: 14, duration: 1 });
    boundsFitted = true;
  }
}

// Global hook for popup auto-dispatch
window.quickDispatchZone = async function (zoneName) {
  try {
    const res = await postJSON("/api/auto-assign/execute", { zone: zoneName });
    showToast("Unit Dispatched", `Team #${res.deployment.team_id} successfully assigned to ${zoneName}`, "Medium");
    refreshDashboard();
  } catch (e) {
    showToast("Dispatch Failed", e.message, "High");
  }
};

// -------------------------------------------------------- Dashboard Widgets ----

function renderGauges(data) {
  const hs = data.hospital_summary || {};
  const rs = data.rescue_summary || {};

  // Hospital Capacity Meter
  const totalBeds = hs.total_beds || 1;
  const availBeds = hs.beds_available || 0;
  const occPct = hs.occupancy_pct || 0;

  const bedsFill = document.getElementById("gauge-beds-fill");
  const bedsText = document.getElementById("gauge-beds-text");
  const occBadge = document.getElementById("hosp-occupancy-badge");

  if (bedsFill) bedsFill.style.width = Math.min(100, Math.max(0, occPct)) + "%";
  if (bedsText) bedsText.textContent = `${availBeds} Available / ${totalBeds} Total`;
  if (occBadge) {
    occBadge.textContent = `${occPct}% Occupancy`;
    occBadge.className = `badge ${occPct > 80 ? 'badge-high' : occPct > 60 ? 'badge-medium' : 'badge-available'}`;
  }

  // Calculate ICU and Doctor availability from all_hospitals
  const hospitals = data.all_hospitals || [];
  const totalIcu = hospitals.reduce((sum, h) => sum + (h.icu_available || 0), 0);
  const totalDocs = hospitals.reduce((sum, h) => sum + (h.doctors_available || 0), 0);

  const icuFill = document.getElementById("gauge-icu-fill");
  const icuText = document.getElementById("gauge-icu-text");
  if (icuFill) icuFill.style.width = Math.min(100, totalIcu * 1.5) + "%";
  if (icuText) icuText.textContent = `${totalIcu} ICU Beds Ready Network-Wide`;

  const docFill = document.getElementById("gauge-doctors-fill");
  const docText = document.getElementById("gauge-doctors-text");
  if (docFill) docFill.style.width = Math.min(100, totalDocs * 0.8) + "%";
  if (docText) docText.textContent = `${totalDocs} Emergency Doctors Active`;

  // Rescue Fleet Readiness Meter
  const availTeams = rs.teams_available || 0;
  const totalTeams = rs.teams_total || 1;
  const deployedCount = (data.active_deployments || []).length;
  const mobilizationPct = Math.round((deployedCount / Math.max(1, totalTeams)) * 100);

  const elAvailTeams = document.getElementById("fleet-avail-teams");
  const elDeployed = document.getElementById("fleet-deployed-teams");
  const elAmbulances = document.getElementById("fleet-ambulances");
  const elFire = document.getElementById("fleet-fire");
  const elMobFill = document.getElementById("gauge-rescue-fill");
  const elMobPct = document.getElementById("fleet-mobilization-pct");
  const elRescueBadge = document.getElementById("rescue-status-badge");

  if (elAvailTeams) elAvailTeams.textContent = availTeams;
  if (elDeployed) elDeployed.textContent = deployedCount;
  if (elAmbulances) elAmbulances.textContent = rs.ambulances_total || 0;
  if (elFire) elFire.textContent = rs.fire_units_total || 0;

  if (elMobFill) elMobFill.style.width = (100 - mobilizationPct) + "%";
  if (elMobPct) elMobPct.textContent = `${mobilizationPct}% Mobilized in Field`;
  if (elRescueBadge) {
    elRescueBadge.textContent = availTeams > 0 ? "Fleet Ready" : "Units At Capacity";
    elRescueBadge.className = `badge ${availTeams > 0 ? 'badge-available' : 'badge-high'}`;
  }
}

function renderAutoAssignmentMatrix(assignments, activeDeployments) {
  const tbody = document.getElementById("auto-assignment-rows");
  if (!tbody) return;

  const entries = Object.values(assignments || {});
  if (!entries.length) {
    tbody.innerHTML = `<tr><td colspan="7" class="empty-state">No monitored zones configured.</td></tr>`;
    return;
  }

  tbody.innerHTML = "";
  entries.forEach((item) => {
    const hosp = item.hospital;
    const team = item.rescue_team;
    const isDeployed = item.is_active_deployment;

    const hospCell = hosp
      ? `<div style="font-weight:600;">${hosp.hospital_name}</div>
         <div class="zone-meta">${hosp.accessibility_status} &bull; ${hosp.contact}</div>`
      : `<span style="color:var(--fog-400);">No facility assigned</span>`;

    const hospEtaCell = hosp
      ? `<div class="mono" style="font-weight:600; color:var(--signal-info);">${hosp.duration_min} min (${hosp.distance_km} km)</div>
         <div class="zone-meta">${hosp.beds_available} beds free &bull; ${hosp.icu_available} ICU</div>`
      : `<span class="zone-meta">&mdash;</span>`;

    const teamCell = team
      ? `<div style="font-weight:600;">${team.team_name}</div>
         <div class="zone-meta">${team.vehicles || 0} veh &bull; ${team.ambulances || 0} amb &bull; ${team.personnel || 0} pax</div>`
      : `<span style="color:var(--fog-400);">No team available</span>`;

    const teamEtaCell = team
      ? `<div class="mono" style="font-weight:600; color:var(--alert-warning);">${team.duration_min || 5} min (${team.distance_km || 0} km)</div>
         <span class="badge ${team.status === 'deployed' ? 'badge-medium' : 'badge-available'}">${team.status}</span>`
      : `<span class="zone-meta">&mdash;</span>`;

    let actionButton = "";
    if (isDeployed) {
      actionButton = `<button class="btn btn-ghost recall-assignment-btn" data-dep-id="${team ? team.deployment_id : ''}" style="padding:4px 9px; font-size:11.5px;">Recall</button>`;
    } else if (team && team.status === "recommended") {
      actionButton = `<button class="btn btn-primary dispatch-assignment-btn" data-zone="${item.zone}" style="padding:4px 9px; font-size:11.5px;">Dispatch</button>`;
    } else {
      actionButton = `<button class="btn btn-ghost" disabled style="padding:4px 9px; font-size:11.5px; opacity:0.5;">Nominal</button>`;
    }

    const row = el(`
      <tr>
        <td>
          <div style="font-weight:600; cursor:pointer;" class="zone-focus-link" data-lat="${item.lat}" data-lon="${item.lon}">${item.zone}</div>
          <div class="zone-meta">Density: ${item.density.toFixed(2)}</div>
        </td>
        <td><span class="badge ${riskClass(item.risk_level)}">${item.risk_level}</span></td>
        <td>${hospCell}</td>
        <td>${hospEtaCell}</td>
        <td>${teamCell}</td>
        <td>${teamEtaCell}</td>
        <td>${actionButton}</td>
      </tr>
    `);

    // Click zone name to focus map
    row.querySelector(".zone-focus-link").addEventListener("click", () => {
      if (map && item.lat && item.lon) {
        map.flyTo([item.lat, item.lon], 14, { duration: 0.6 });
        if (zoneMarkers[item.zone]) zoneMarkers[item.zone].openPopup();
      }
    });

    tbody.appendChild(row);
  });

  // Attach dispatch button handlers
  tbody.querySelectorAll(".dispatch-assignment-btn").forEach((btn) => {
    btn.addEventListener("click", async () => {
      btn.disabled = true;
      btn.textContent = "Dispatching…";
      try {
        await postJSON("/api/auto-assign/execute", { zone: btn.dataset.zone });
        showToast("Unit Dispatched", `Response force dispatched to ${btn.dataset.zone}`, "Medium");
        refreshDashboard();
      } catch (e) {
        showToast("Error", e.message, "High");
        btn.disabled = false;
        btn.textContent = "Dispatch";
      }
    });
  });

  // Attach recall button handlers
  tbody.querySelectorAll(".recall-assignment-btn").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const depId = parseInt(btn.dataset.depId, 10);
      if (!depId) return;
      btn.disabled = true;
      btn.textContent = "Recalling…";
      try {
        await postJSON("/api/recall", { deployment_id: depId });
        showToast("Team Recalled", "Response team returned to standby.", "Low");
        refreshDashboard();
      } catch (e) {
        showToast("Recall Error", e.message, "High");
        btn.disabled = false;
        btn.textContent = "Recall";
      }
    });
  });
}

function updateEmergencyTicker(data) {
  const tickerText = document.getElementById("ticker-text");
  const tickerTime = document.getElementById("ticker-time");
  if (!tickerText) return;

  const worst = [...(data.weather || [])].sort((a, b) => b.risk_score - a.risk_score)[0];
  const activeAlert = (data.active_alerts || [])[0];

  if (activeAlert && activeAlert.severity === "High") {
    tickerText.innerHTML = `<span style="color:#FF5470; font-weight:700;">[CRITICAL ALERT]</span> ${activeAlert.title} &mdash; ${activeAlert.message}`;
  } else if (worst && worst.risk_level === "High") {
    tickerText.innerHTML = `<span style="color:#FF5470; font-weight:700;">[HIGH RISK]</span> ${worst.zone}: ${worst.prediction} danger elevated (${Math.round(worst.risk_score * 100)}%). Auto-routing hospitals and rescue units.`;
  } else if (worst && worst.risk_level === "Medium") {
    tickerText.innerHTML = `<span style="color:#FFB238; font-weight:700;">[ADVISORY]</span> ${worst.zone}: Elevated ${worst.prediction} probability. Monitoring regional telemetry.`;
  } else {
    tickerText.textContent = `All regional sectors reporting nominal conditions. Multi-agent sensors active.`;
  }

  if (tickerTime) tickerTime.textContent = new Date().toLocaleTimeString("en-GB");
}

function initAutoAssignmentControls() {
  // Batch Auto-Assign All Button
  const btnAll = document.getElementById("auto-assign-all-btn");
  if (btnAll) {
    btnAll.addEventListener("click", async () => {
      btnAll.disabled = true;
      btnAll.innerHTML = `<span class="spinner"></span> Auto-Assigning All Units…`;
      try {
        const res = await postJSON("/api/auto-assign/execute", { all: true });
        showToast("Batch Auto-Assignment Complete", `Successfully dispatched ${res.dispatched_count} rescue unit(s) and reserved hospital capacity.`, "Medium");
        refreshDashboard();
      } catch (e) {
        showToast("Auto-Assign Error", e.message, "High");
      } finally {
        btnAll.disabled = false;
        btnAll.innerHTML = `<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2"><path d="M13 2L3 14h9l-1 8 10-12h-9l1-8z"/></svg> Auto-Assign All Units &amp; Routes`;
      }
    });
  }

  // Autonomous Dispatch Toggle Switch
  const toggle = document.getElementById("auto-dispatch-toggle");
  const label = document.getElementById("auto-dispatch-label");
  if (toggle) {
    toggle.addEventListener("change", async () => {
      const enabled = toggle.checked;
      try {
        const res = await postJSON("/api/auto-assign/toggle", { enabled });
        currentAutoDispatchActive = res.auto_dispatch_active;
        if (label) label.textContent = `Autonomous Dispatch: ${currentAutoDispatchActive ? 'ON' : 'OFF'}`;
        showToast(
          "Autonomous Dispatch Mode",
          currentAutoDispatchActive
            ? "Autonomous mode ACTIVE: Nearest rescue teams & hospitals will auto-dispatch on High risk."
            : "Autonomous mode STANDBY: Manual staff confirmation required.",
          currentAutoDispatchActive ? "Medium" : "Low"
        );
      } catch (e) {
        toggle.checked = !enabled;
        showToast("Toggle Failed", e.message, "High");
      }
    });
  }
}

// ---------------------------------------------------- Emergency Broadcast Modal ----

function initBroadcastModal() {
  const openBtn = document.getElementById("open-broadcast-btn");
  const modal = document.getElementById("broadcast-modal");
  const closeBtn = document.getElementById("close-broadcast-modal");
  const cancelBtn = document.getElementById("cancel-broadcast-btn");
  const sendBtn = document.getElementById("send-broadcast-btn");
  const zoneSelect = document.getElementById("bc-zone");

  if (!modal) return;

  function closeModal() {
    modal.style.display = "none";
  }

  if (openBtn) {
    openBtn.addEventListener("click", async () => {
      modal.style.display = "flex";
      // Populate zones dropdown
      try {
        const zones = await getJSON("/api/zones");
        if (zoneSelect) {
          zoneSelect.innerHTML = `<option value="">City-Wide (All Monitored Districts)</option>`;
          zones.forEach((z) => {
            zoneSelect.innerHTML += `<option value="${z.name}">${z.name}</option>`;
          });
        }
      } catch (e) {
        console.warn("Zones load failed for broadcast modal:", e);
      }
    });
  }

  if (closeBtn) closeBtn.addEventListener("click", closeModal);
  if (cancelBtn) cancelBtn.addEventListener("click", closeModal);

  if (sendBtn) {
    sendBtn.addEventListener("click", async () => {
      const title = (document.getElementById("bc-title").value || "").trim();
      const message = (document.getElementById("bc-msg").value || "").trim();
      const zone = document.getElementById("bc-zone").value || null;

      const channels = [];
      if (document.getElementById("bc-ch-push")?.checked) channels.push("push");
      if (document.getElementById("bc-ch-sms")?.checked) channels.push("sms");
      if (document.getElementById("bc-ch-whatsapp")?.checked) channels.push("whatsapp");
      if (document.getElementById("bc-ch-email")?.checked) channels.push("email");

      if (!title || !message) {
        alert("Please provide both a broadcast title and emergency instructions.");
        return;
      }

      sendBtn.disabled = true;
      sendBtn.textContent = "Broadcasting…";

      try {
        await postJSON("/api/notifications/broadcast", { title, message, zone, channels });
        showToast("Emergency Broadcast Sent", `Broadcast dispatched across ${channels.join(", ").toUpperCase()}`, "High");
        closeModal();
        document.getElementById("bc-title").value = "";
        document.getElementById("bc-msg").value = "";
        refreshDashboard();
      } catch (e) {
        alert("Broadcast failed: " + e.message);
      } finally {
        sendBtn.disabled = false;
        sendBtn.textContent = "Send Immediate Broadcast";
      }
    });
  }
}

// ---------------------------------------------------- Real-Time Push Stream ----

function initEventSource() {
  function handleHighRisk(data) {
    playEmergencyChime();
    showToast(data.title, data.message, "High", 10000);

    if ("Notification" in window && Notification.permission === "granted") {
      new Notification(data.title, {
        body: data.message,
        icon: "/static/img/brand_mark.png",
      });
    }
    refreshDashboard();
  }

  function handleBroadcast(data) {
    playEmergencyChime();
    showToast(`🚨 BROADCAST: ${data.title}`, data.message, "High", 12000);

    if ("Notification" in window && Notification.permission === "granted") {
      new Notification(`EMERGENCY BROADCAST: ${data.title}`, {
        body: data.message,
        icon: "/static/img/brand_mark.png",
      });
    }
    refreshDashboard();
  }

  window.addEventListener("nirvaha:high_risk_alert", (evt) => handleHighRisk(evt.detail));
  window.addEventListener("nirvaha:emergency_broadcast", (evt) => handleBroadcast(evt.detail));
}

// ------------------------------------------------ Hospital Capacity Chart ----

function renderHospitalCapacityChart(data) {
  const container = document.getElementById("hosp-capacity-bars");
  const totalLabel = document.getElementById("hosp-chart-total-label");
  if (!container) return;

  const hospitals = data.all_hospitals || [];
  if (!hospitals.length) {
    container.innerHTML = `<div class="empty-state">No hospital facilities configured.</div>`;
    return;
  }

  const totalBeds = hospitals.reduce((sum, h) => sum + (h.beds_total || 0), 0);
  const availBeds = hospitals.reduce((sum, h) => sum + (h.beds_available || 0), 0);
  if (totalLabel) {
    totalLabel.textContent = `${availBeds} / ${totalBeds} Beds Free (${Math.round((availBeds / Math.max(1, totalBeds)) * 100)}%)`;
  }

  container.innerHTML = "";
  hospitals.forEach((h) => {
    const total = h.beds_total || 100;
    const avail = h.beds_available || 0;
    const occupied = Math.max(0, total - avail);
    const occPct = Math.round((occupied / total) * 100);
    const barColor = occPct > 85 ? "var(--alert-critical)" : (occPct > 65 ? "var(--alert-warning)" : "var(--alert-nominal)");

    container.appendChild(el(`
      <div class="hosp-cap-card">
        <div class="hosp-cap-header">
          <div>
            <div class="hosp-cap-name">🏥 ${h.hospital_name}</div>
            <div class="hosp-cap-loc">${h.location} &bull; ${h.accessibility_status || 'Operational'}</div>
          </div>
          <span class="badge ${occPct > 85 ? 'badge-high' : (occPct > 65 ? 'badge-medium' : 'badge-available')} mono">${occPct}% Occupied</span>
        </div>
        <div class="hosp-cap-bar-track">
          <div class="hosp-cap-bar-fill" style="width:${occPct}%; background:${barColor};"></div>
        </div>
        <div class="hosp-cap-stats mono">
          <span>Available: <b>${avail}</b> / ${total}</span>
          <span>ICU: <b>${h.icu_available || 0}</b></span>
          <span>Doctors: <b>${h.doctors_available || 0}</b></span>
        </div>
      </div>
    `));
  });
}

// --------------------------------------------- Deployment Status Timeline ----

function renderDeploymentTimeline(data) {
  const list = document.getElementById("deployment-timeline-list");
  const badge = document.getElementById("timeline-count-badge");
  if (!list) return;

  const active = data.active_deployments || [];
  if (badge) badge.textContent = `${active.length} Active Field Units`;

  if (!active.length) {
    list.innerHTML = `<div class="empty-state">No rescue deployments currently fielding in the district.</div>`;
    return;
  }

  list.innerHTML = "";
  active.forEach((dep) => {
    list.appendChild(el(`
      <div class="timeline-entry">
        <div class="timeline-dot dot-active"></div>
        <div class="timeline-header">
          <span class="timeline-title">🚒 Team #${dep.team_id} &rarr; ${dep.zone}</span>
          <span class="timeline-time mono">${fmtTime(dep.deployed_at)}</span>
        </div>
        <div class="timeline-detail">
          Dispatched by <strong>${dep.deployed_by || 'Command'}</strong> &bull;
          Reserved <strong>${dep.beds_reserved || 0} beds</strong> at receiving center &bull;
          Status: <span class="badge badge-medium" style="font-size:10px;">ACTIVE FIELD OP</span>
        </div>
      </div>
    `));
  });
}

// ------------------------------------ IoT & Social Intelligence Widgets ----

function renderIoTAndSocial(data) {
  const iotList = document.getElementById("iot-sensors-list");
  const iotBadge = document.getElementById("iot-sensor-count-badge");
  const socialList = document.getElementById("social-distress-stream");
  const socialBadge = document.getElementById("social-sos-count-badge");

  const sensors = data.iot_sensors || [];
  if (iotBadge) {
    const critCount = sensors.filter((s) => s.status === "Critical").length;
    iotBadge.textContent = `${sensors.length} Stations (${critCount} Alert)`;
    iotBadge.className = critCount > 0 ? "badge badge-high mono" : "badge badge-info mono";
  }

  if (iotList) {
    if (!sensors.length) {
      iotList.innerHTML = `<div class="empty-state">No IoT telemetry streams active.</div>`;
    } else {
      iotList.innerHTML = "";
      sensors.forEach((s) => {
        const badgeClass = s.status === "Critical" ? "badge-high" : (s.status === "Warning" ? "badge-medium" : "badge-available");
        const progressColor = s.status === "Critical" ? "var(--alert-critical)" : (s.status === "Warning" ? "var(--alert-warning)" : "var(--alert-nominal)");
        const barWidth = Math.min(100, Math.max(5, s.fill_percentage));

        const card = el(`
          <div style="background:var(--card-bg-subtle, rgba(255,255,255,0.03)); border:1px solid var(--border-color, rgba(255,255,255,0.08)); border-radius:8px; padding:10px 12px;">
            <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:6px;">
              <span style="font-weight:600; font-size:13px; color:var(--text-bright, #fff);">📡 ${s.sensor_name}</span>
              <span class="badge ${badgeClass}" style="font-size:10px;">${s.status.toUpperCase()}</span>
            </div>
            <div style="display:flex; justify-content:space-between; font-size:11px; color:var(--text-muted, #94a3b8); margin-bottom:4px;">
              <span>Zone: <strong>${s.zone}</strong> &bull; ${s.sensor_type}</span>
              <span class="mono"><strong>${s.current_value} ${s.unit}</strong> / ${s.critical_threshold} ${s.unit}</span>
            </div>
            <div style="width:100%; height:6px; background:rgba(255,255,255,0.08); border-radius:3px; overflow:hidden;">
              <div style="width:${barWidth}%; height:100%; background:${progressColor}; transition:width 0.3s ease;"></div>
            </div>
            <div style="display:flex; justify-content:space-between; font-size:10px; color:var(--text-muted, #64748b); margin-top:4px;">
              <span>Hardware: ${s.sensor_id}</span>
              <span>🔋 ${s.battery_pct}% &bull; Updated ${fmtTime(s.last_reading_time)}</span>
            </div>
          </div>
        `);
        iotList.appendChild(card);
      });
    }
  }

  latestDashboardData = data;

  let distress = data.social_distress || [];
  if (currentSocialPlatform === "verified") {
    distress = distress.filter((p) => Boolean(p.verified));
  } else if (currentSocialPlatform !== "all") {
    distress = distress.filter((p) => (p.platform || "").toLowerCase() === currentSocialPlatform.toLowerCase());
  }

  if (socialBadge) {
    socialBadge.textContent = `${distress.length} Live Items`;
  }

  if (socialList) {
    if (!distress.length) {
      socialList.innerHTML = `<div class="empty-state">No verified emergency reports found for the selected channel.</div>`;
    } else {
      socialList.innerHTML = "";
      distress.forEach((p) => {
        const platform = (p.platform || "Twitter").trim();
        let platIcon = "𝕏";
        let platClass = "badge-platform-twitter";
        let platLabel = "Twitter";

        if (platform.toLowerCase() === "instagram") {
          platIcon = "📸";
          platClass = "badge-platform-instagram";
          platLabel = "Instagram";
        } else if (platform.toLowerCase() === "facebook") {
          platIcon = "📘";
          platClass = "badge-platform-facebook";
          platLabel = "Facebook";
        }

        const verifiedBadge = p.verified ? `<span class="verified-badge">✓ Official Agency</span>` : "";
        const urgencyBadgeClass = p.urgency_level === "Critical" ? "badge-high" : (p.urgency_level === "High" ? "badge-medium" : "badge-info");
        const sourceLink = p.post_url ? `<a href="${p.post_url}" target="_blank" rel="noopener noreferrer" style="font-size:11px; color:var(--signal-info, #5EA8FF); text-decoration:none; display:inline-flex; align-items:center; gap:3px;">🔗 View Source ↗</a>` : "";

        const item = el(`
          <div class="social-post-card">
            <div style="display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:6px; margin-bottom:6px;">
              <div style="display:flex; align-items:center; gap:6px;">
                <span class="social-platform-badge ${platClass}">${platIcon} ${platLabel}</span>
                ${verifiedBadge}
                <span style="font-weight:700; font-size:12px; color:var(--text-bright, #fff);">${p.username}</span>
              </div>
              <div style="display:flex; align-items:center; gap:6px;">
                <span class="badge ${urgencyBadgeClass}" style="font-size:10px;">${p.urgency_level}</span>
                <span class="badge badge-info" style="font-size:10px;">📍 ${p.zone}</span>
              </div>
            </div>
            <div style="font-size:12.5px; color:var(--signal-100, #f1f5f9); line-height:1.45; margin-bottom:8px;">
              "${p.message}"
            </div>
            <div style="display:flex; justify-content:space-between; align-items:center; font-size:10.5px; color:var(--fog-400, #94a3b8); padding-top:4px; border-top:1px solid rgba(255,255,255,0.05);">
              <span class="mono">Logged: ${fmtTime(p.created_at)}</span>
              <div style="display:flex; align-items:center; gap:10px;">
                ${sourceLink}
                <button class="btn btn-ghost" onclick="quickDispatchZone('${p.zone}')" style="padding:2px 8px; font-size:10.5px; color:var(--alert-warning, #FFB238); border-color:rgba(255,178,56,0.3);">
                  🚨 Dispatch Unit
                </button>
              </div>
            </div>
          </div>
        `);
        socialList.appendChild(item);
      });
    }
  }
}

// ------------------------------------ Social Media Sync & Filter Controls ----

function initSocialControls() {
  document.querySelectorAll(".social-filter-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      document.querySelectorAll(".social-filter-btn").forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      currentSocialPlatform = btn.dataset.platform;
      if (latestDashboardData) {
        renderIoTAndSocial(latestDashboardData);
      }
    });
  });

  const pullBtn = document.getElementById("pull-social-feeds-btn");
  if (pullBtn) {
    pullBtn.addEventListener("click", async () => {
      const icon = document.getElementById("pull-icon");
      if (icon) icon.textContent = "⏳";
      try {
        pullBtn.disabled = true;
        pullBtn.style.opacity = "0.6";
        const res = await postJSON("/api/social/sync");
        showToast("Social Feeds Synced", `Pulled ${res.pulled_count || 0} live verified updates across Twitter, Instagram & Facebook.`, "Low");
        await refreshDashboard();
      } catch (e) {
        showToast("Sync Completed", e.message || "Social streams updated.", "Low");
        await refreshDashboard();
      } finally {
        pullBtn.disabled = false;
        pullBtn.style.opacity = "1";
        if (icon) icon.textContent = "🔄";
      }
    });
  }
}

// ------------------------------------------------------------- Main Refresh ----

async function refreshDashboard() {
  try {
    const data = await getJSON("/api/dashboard");

    setGlobalRiskPill(data.overall_risk);

    // Active Incidents Metric Card
    const hazardZones = (data.weather || []).filter((w) => w.risk_level === "High" || w.risk_level === "Medium").length;
    const activeDeploymentsCount = (data.active_deployments || []).length;
    const totalActiveIncidents = hazardZones + activeDeploymentsCount;
    const elInc = document.getElementById("m-incidents");
    const elIncSub = document.getElementById("m-incidents-sub");
    if (elInc) elInc.textContent = totalActiveIncidents;
    if (elIncSub) elIncSub.textContent = `${hazardZones} hazard zone(s) · ${activeDeploymentsCount} deployment(s)`;

    // Top 4 Metric Cards
    const worstWeather = [...data.weather].sort((a, b) => b.risk_score - a.risk_score)[0];
    document.getElementById("m-weather-risk").textContent = worstWeather.risk_level;
    document.getElementById("m-weather-zone").textContent = `${worstWeather.zone} · ${worstWeather.prediction}`;

    const worstTraffic = [...data.traffic].sort((a, b) => {
      const order = { Low: 0, Moderate: 1, High: 2, Severe: 3 };
      return order[b.congestion_level] - order[a.congestion_level];
    })[0];
    document.getElementById("m-traffic-status").textContent = worstTraffic.congestion_level;
    document.getElementById("m-traffic-zone").textContent = `${worstTraffic.zone} · ${worstTraffic.road_status}`;

    document.getElementById("m-beds").textContent = data.hospital_summary.beds_available;
    document.getElementById("m-beds-sub").textContent = `${data.hospital_summary.occupancy_pct}% network capacity`;

    document.getElementById("m-teams").textContent = `${data.rescue_summary.teams_available} / ${data.rescue_summary.teams_total}`;
    document.getElementById("m-teams-sub").textContent = `${(data.active_deployments || []).length} deployed in field`;

    // Auto-dispatch toggle sync
    const toggle = document.getElementById("auto-dispatch-toggle");
    const label = document.getElementById("auto-dispatch-label");
    if (toggle && label) {
      currentAutoDispatchActive = Boolean(data.auto_dispatch_active);
      toggle.checked = currentAutoDispatchActive;
      label.textContent = `Autonomous Dispatch: ${currentAutoDispatchActive ? 'ON' : 'OFF'}`;
    }

    // Render Advanced Widgets
    renderGauges(data);
    updateEmergencyTicker(data);
    renderAutoAssignmentMatrix(data.zone_assignments, data.active_deployments);
    renderHospitalCapacityChart(data);
    renderDeploymentTimeline(data);
    renderIoTAndSocial(data);

    // Zone Priorities & Deployments legacy panels
    if (typeof renderZonePriorities === "function") renderZonePriorities(data.zone_priorities);
    if (typeof renderDeployment === "function") renderDeployment(data.deployment_recommendations, data.active_deployments);

    // Unified Command Map
    renderUnifiedMap(data);
  } catch (e) {
    console.error("Dashboard refresh error:", e);
  }
}

// ------------------------------------------------------------- Init Sequence ----

document.addEventListener("DOMContentLoaded", () => {
  initMap();
  initAudioControls();
  initPushControls();
  initAutoAssignmentControls();
  initBroadcastModal();
  initSocialControls();
  initEventSource();
  refreshDashboard();
  setInterval(refreshDashboard, POLL_INTERVAL_MS);
});
