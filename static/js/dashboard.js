let map, zoneLayer, hospitalLayer, teamLayer, routeLayer;
let mapReady = false;
let zoneMarkers = {};    // zone name -> Leaflet circleMarker, kept across polls
let hospitalMarkers = {}; // hospital id -> Leaflet marker
let boundsFittedForZoneCount = 0;

function initMap() {
  // A failed/blocked map tile CDN used to take the whole dashboard down —
  // initMap() threw before refreshDashboard() ever ran, so metric cards,
  // zone priorities, and deployment actions never rendered either. Now a
  // map failure only affects the map panel.
  try {
    if (typeof L === "undefined") throw new Error("Leaflet failed to load");
    map = L.map("map", {
      zoomControl: true,
      attributionControl: false,
      zoomAnimation: true,
      fadeAnimation: true,
      markerZoomAnimation: true,
    }).setView([12.9121, 77.6446], 12);
    L.tileLayer("https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png", {
      maxZoom: 18,
    }).addTo(map);
    zoneLayer = L.layerGroup().addTo(map);
    hospitalLayer = L.layerGroup().addTo(map);
    teamLayer = L.layerGroup().addTo(map);
    routeLayer = L.layerGroup().addTo(map);
    mapReady = true;
  } catch (e) {
    console.warn("Map unavailable:", e.message);
    const el = document.getElementById("map");
    if (el) el.innerHTML = '<div class="empty-state">Map tiles unavailable — check browser internet access.</div>';
  }
}

function riskColor(level) {
  return { Low: "#2DD4BF", Medium: "#FFB238", High: "#FF5470" }[level] || "#5EA8FF";
}

// Marker size reflects actual severity instead of every zone looking the
// same regardless of risk — a High-risk zone should visually read as more
// urgent than a Low one at a glance, not just via a tooltip you have to open.
function riskRadius(level) {
  return { Low: 9, Medium: 13, High: 18 }[level] || 9;
}

function renderZonePriorities(priorities) {
  const container = document.getElementById("zone-priority-list");
  container.innerHTML = "";
  priorities.forEach((p, idx) => {
    container.appendChild(
      el(`
        <div class="zone-row">
          <div>
            <div class="zone-name">#${idx + 1} · ${p.zone}</div>
            <div class="zone-meta">Population density weight ${p.density.toFixed(2)} · priority score ${p.priority_score.toFixed(2)}</div>
          </div>
          <span class="badge ${riskClass(p.risk_level)}">${p.risk_level} risk</span>
        </div>
      `)
    );
  });
}

function renderDeployment(deployments, activeDeployments) {
  const container = document.getElementById("deployment-panel");
  container.innerHTML = "";

  if (activeDeployments && activeDeployments.length) {
    const activeWrap = el(`<div style="margin-bottom:14px;"><div class="metric-label" style="margin-bottom:8px;">Currently deployed</div></div>`);
    activeDeployments.forEach((d) => {
      activeWrap.appendChild(
        el(`
          <div class="zone-row" style="margin-bottom:8px;">
            <div>
              <div class="zone-name">Team #${d.team_id} → ${d.zone}</div>
              <div class="zone-meta">Dispatched ${fmtTime(d.deployed_at)}${d.deployed_by ? " by " + d.deployed_by : ""}</div>
            </div>
            <button class="btn btn-ghost recall-btn" data-deployment-id="${d.id}" style="padding:6px 12px; font-size:12.5px;">Recall</button>
          </div>
        `)
      );
    });
    container.appendChild(activeWrap);
  }

  if (!deployments.length) {
    container.appendChild(el(`<div class="empty-state">No new deployment needed — all zones nominal.</div>`));
  } else {
    deployments.forEach((d) => {
      container.appendChild(
        el(`
          <div class="zone-row" style="margin-bottom:10px;">
            <div>
              <div class="zone-name">${d.team_name} → ${d.zone}</div>
              <div class="zone-meta">${d.distance_km} km away · ${d.vehicles} vehicles · ${d.ambulances} ambulances · ${d.fire_units} fire units · ${d.personnel} personnel</div>
            </div>
            <div style="display:flex; align-items:center; gap:8px;">
              <span class="badge ${riskClass(d.risk_level)}">${d.risk_level}</span>
              <button class="btn btn-primary deploy-btn" data-zone="${d.zone}" style="padding:6px 12px; font-size:12.5px;">Deploy</button>
            </div>
          </div>
        `)
      );
    });
  }

  container.querySelectorAll(".deploy-btn").forEach((btn) => {
    btn.addEventListener("click", async () => {
      btn.disabled = true;
      btn.textContent = "Dispatching…";
      try {
        await postJSON("/api/deploy", { zone: btn.dataset.zone });
        await refreshDashboard();
      } catch (e) {
        alert(e.message);
        btn.disabled = false;
        btn.textContent = "Deploy";
      }
    });
  });

  container.querySelectorAll(".recall-btn").forEach((btn) => {
    btn.addEventListener("click", async () => {
      btn.disabled = true;
      btn.textContent = "Recalling…";
      try {
        await postJSON("/api/recall", { deployment_id: parseInt(btn.dataset.deploymentId, 10) });
        await refreshDashboard();
      } catch (e) {
        alert(e.message);
        btn.disabled = false;
        btn.textContent = "Recall";
      }
    });
  });
}

function renderHospitalRecs(hospitals) {
  const container = document.getElementById("hospital-recs");
  if (!hospitals.length) {
    container.innerHTML = `<div class="empty-state">No hospital data yet.</div>`;
    return;
  }
  container.innerHTML = "";
  hospitals.forEach((h) => {
    const atRisk = h.accessibility_risk && h.accessibility_risk !== "Low";
    const warning = atRisk
      ? `<div class="zone-meta" style="color:var(--alert-warning); margin-top:2px;">⚠ ${h.accessibility_status}</div>`
      : "";
    container.appendChild(
      el(`
        <div class="zone-row" style="margin-bottom:10px; ${atRisk ? "border-color:rgba(255,178,56,0.4);" : ""}">
          <div>
            <div class="zone-name">${h.hospital_name}</div>
            <div class="zone-meta">${h.distance_km} km · ${h.beds_available}/${h.beds_total} beds · ${h.icu_available} ICU · ${h.doctors_available} doctors</div>
            ${warning}
          </div>
          <span class="badge badge-info">${h.suitability}</span>
        </div>
      `)
    );
  });
}

function renderMap(data) {
  if (!mapReady) return;

  // Persistent markers, updated in place, instead of clearing and
  // recreating every 15s poll — that used to cause a visible flash/flicker
  // on every refresh even when nothing changed. Combined with the CSS
  // transition on .leaflet-interactive (style.css), a risk-level color or
  // radius change now fades smoothly instead of snapping.
  const seenZones = new Set();
  data.weather.forEach((w) => {
    seenZones.add(w.zone);
    const popup = `<b>${w.zone}</b><br>${w.prediction} · ${w.risk_level} risk (${Math.round(w.risk_score * 100)}%)<br>Rainfall ${w.weather.rainfall_mm}mm · Wind ${w.weather.wind_speed_kmh}km/h`;
    let marker = zoneMarkers[w.zone];
    if (!marker) {
      marker = L.circleMarker([w.lat, w.lon], {
        radius: riskRadius(w.risk_level),
        color: riskColor(w.risk_level),
        fillColor: riskColor(w.risk_level),
        fillOpacity: 0.35,
        weight: 2,
      }).bindPopup(popup).addTo(zoneLayer);
      zoneMarkers[w.zone] = marker;
    } else {
      marker.setStyle({ radius: riskRadius(w.risk_level), color: riskColor(w.risk_level), fillColor: riskColor(w.risk_level) });
      marker.setLatLng([w.lat, w.lon]); // zones can move if an admin edits lat/lon
      marker.setPopupContent(popup);
    }
    const el = marker.getElement && marker.getElement();
    if (el) el.classList.toggle("risk-pulse-high", w.risk_level === "High");
  });
  // A zone removed via /admin/zones should disappear from the map too.
  Object.keys(zoneMarkers).forEach((name) => {
    if (!seenZones.has(name)) {
      zoneLayer.removeLayer(zoneMarkers[name]);
      delete zoneMarkers[name];
    }
  });

  const seenHospitals = new Set();
  (data.recommended_hospitals || []).forEach((h) => {
    seenHospitals.add(h.id);
    const popup = `<b>${h.hospital_name}</b><br>${h.beds_available} beds available<br>${h.accessibility_status || "Operational"}`;
    let marker = hospitalMarkers[h.id];
    if (!marker) {
      marker = L.marker([h.lat, h.lon], { icon: L.divIcon({ className: "", html: "🏥", iconSize: [20, 20] }) })
        .bindPopup(popup)
        .addTo(hospitalLayer);
      hospitalMarkers[h.id] = marker;
    } else {
      marker.setPopupContent(popup);
    }
  });
  Object.keys(hospitalMarkers).forEach((id) => {
    if (!seenHospitals.has(Number(id))) {
      hospitalLayer.removeLayer(hospitalMarkers[id]);
      delete hospitalMarkers[id];
    }
  });

  routeLayer.clearLayers(); // the route itself is a one-off overlay, not a tracked entity
  if (data.recommended_route && data.recommended_route.path) {
    L.polyline(data.recommended_route.path, { color: "#5EA8FF", weight: 3, dashArray: "6 6" }).addTo(routeLayer);
  }

  // Fit the view to whatever zones actually exist, once per zone-count
  // change, instead of a hardcoded center/zoom that assumes exactly the 5
  // demo zones — an admin adding a 6th zone somewhere else should still
  // be visible on the map without anyone manually re-centering it.
  const zoneCount = data.weather.length;
  if (zoneCount > 0 && zoneCount !== boundsFittedForZoneCount) {
    const bounds = L.latLngBounds(data.weather.map((w) => [w.lat, w.lon]));
    map.flyToBounds(bounds, { padding: [40, 40], maxZoom: 14, duration: 0.8 });
    boundsFittedForZoneCount = zoneCount;
  }
}

async function refreshDashboard() {
  try {
    const data = await getJSON("/api/dashboard");

    setGlobalRiskPill(data.overall_risk);

    const worstWeather = [...data.weather].sort(
      (a, b) => b.risk_score - a.risk_score
    )[0];
    document.getElementById("m-weather-risk").textContent = worstWeather.risk_level;
    document.getElementById("m-weather-zone").textContent =
      worstWeather.zone + " · " + worstWeather.prediction;

    const worstTraffic = [...data.traffic].sort((a, b) => {
      const order = { Low: 0, Moderate: 1, High: 2, Severe: 3 };
      return order[b.congestion_level] - order[a.congestion_level];
    })[0];
    document.getElementById("m-traffic-status").textContent = worstTraffic.congestion_level;
    document.getElementById("m-traffic-zone").textContent =
      worstTraffic.zone + " · " + worstTraffic.road_status;

    document.getElementById("m-beds").textContent = data.hospital_summary.beds_available;
    document.getElementById("m-teams").textContent =
      data.rescue_summary.teams_available + " / " + data.rescue_summary.teams_total;

    renderZonePriorities(data.zone_priorities);
    renderDeployment(data.deployment_recommendations, data.active_deployments);
    renderHospitalRecs(data.recommended_hospitals);
    renderMap(data);
  } catch (e) {
    console.error(e);
  }
}

initMap();
refreshDashboard();
setInterval(refreshDashboard, POLL_INTERVAL_MS);
