let map, zoneLayer, hospitalLayer, teamLayer, routeLayer;
let mapReady = false;

function initMap() {
  // A failed/blocked map tile CDN used to take the whole dashboard down —
  // initMap() threw before refreshDashboard() ever ran, so metric cards,
  // zone priorities, and deployment actions never rendered either. Now a
  // map failure only affects the map panel.
  try {
    if (typeof L === "undefined") throw new Error("Leaflet failed to load");
    map = L.map("map", { zoomControl: true, attributionControl: false }).setView([12.9121, 77.6446], 12);
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
  zoneLayer.clearLayers();
  hospitalLayer.clearLayers();
  teamLayer.clearLayers();
  routeLayer.clearLayers();

  data.weather.forEach((w) => {
    L.circleMarker([w.lat, w.lon], {
      radius: 12,
      color: riskColor(w.risk_level),
      fillColor: riskColor(w.risk_level),
      fillOpacity: 0.35,
      weight: 2,
    })
      .bindPopup(`<b>${w.zone}</b><br>${w.prediction} risk: ${w.risk_level}<br>Rainfall ${w.weather.rainfall_mm}mm · Wind ${w.weather.wind_speed_kmh}km/h`)
      .addTo(zoneLayer);
  });

  (data.recommended_hospitals || []).forEach((h) => {
    L.marker([h.lat, h.lon], {
      icon: L.divIcon({ className: "", html: "🏥", iconSize: [20, 20] }),
    })
      .bindPopup(`<b>${h.hospital_name}</b><br>${h.beds_available} beds available`)
      .addTo(hospitalLayer);
  });

  if (data.recommended_route && data.recommended_route.path) {
    L.polyline(data.recommended_route.path, { color: "#5EA8FF", weight: 3, dashArray: "6 6" }).addTo(routeLayer);
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
