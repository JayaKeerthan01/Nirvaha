let evacMap = null;
let evacLayerGroup = null;

function initEvacMap() {
  if (evacMap) return;
  const container = document.getElementById("evacuation-map");
  if (!container || typeof L === "undefined") return;

  evacMap = L.map("evacuation-map", {
    zoomControl: true,
    attributionControl: true
  }).setView([12.9716, 77.5946], 12);

  L.tileLayer("https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png", {
    attribution: '&copy; <a href="https://carto.com/">CARTO</a> &copy; OpenStreetMap',
    maxZoom: 19,
    subdomains: "abcd"
  }).addTo(evacMap);

  evacLayerGroup = L.layerGroup().addTo(evacMap);
}

async function findCitizenShelterAndRoute() {
  const fromZone = document.getElementById("from-zone").value;
  const shelterBox = document.getElementById("shelter-result");
  const corridorBox = document.getElementById("corridor-result");
  const hospGrid = document.getElementById("citizen-hospitals-grid");
  const sumName = document.getElementById("summary-route-name");
  const sumMet = document.getElementById("summary-route-metrics");

  shelterBox.innerHTML = `<div class="empty-state">Finding closest designated relief shelter…</div>`;
  corridorBox.innerHTML = `<div class="empty-state">Computing congestion-avoiding corridor…</div>`;
  hospGrid.innerHTML = `<div class="empty-state">Checking reachable medical centers…</div>`;
  if (sumName) sumName.textContent = `Routing from ${fromZone}…`;

  initEvacMap();
  if (evacLayerGroup) evacLayerGroup.clearLayers();
  const bounds = [];

  // 1. Fetch Relief Shelter Route & Plot on Map
  try {
    const sData = await getJSON(`/api/citizen/shelter?zone=${encodeURIComponent(fromZone)}`);
    if (sData && sData.shelter) {
      const s = sData.shelter;
      const occPct = Math.round((s.current_occupancy / Math.max(1, s.capacity)) * 100);
      shelterBox.innerHTML = `
        <div class="risk-card-lg risk-low" style="margin-bottom:12px;">
          <div>
            <div class="zone-name">🏠 ${s.name}</div>
            <div class="zone-detail">${s.address || s.name} &bull; Zone: <strong>${s.zone}</strong></div>
            <div class="zone-detail" style="margin-top:4px;">
              Contact: <strong>${s.contact || 'Disaster Control'}</strong> &bull; Occupancy: <strong>${s.current_occupancy} / ${s.capacity} (${occPct}%)</strong>
            </div>
          </div>
          <div class="risk-word">${occPct < 85 ? 'Open' : 'Limited'}</div>
        </div>
        <div style="background:rgba(255,255,255,0.03); border:1px solid var(--border-subtle); border-radius:8px; padding:12px; font-size:13px;">
          <div style="display:flex; justify-content:space-between; margin-bottom:6px;">
            <span>Estimated Travel Distance:</span>
            <strong class="mono">${sData.distance_km} km</strong>
          </div>
          <div style="display:flex; justify-content:space-between; margin-bottom:6px;">
            <span>Expected Travel Time:</span>
            <strong class="mono" style="color:var(--signal-info);">${sData.duration_min} min</strong>
          </div>
          <div style="display:flex; justify-content:space-between;">
            <span>Traffic Condition:</span>
            <span class="badge ${sData.congestion_level === 'Low' ? 'badge-low' : 'badge-medium'}">${sData.congestion_level} Traffic</span>
          </div>
          ${sData.hazard_avoidance ? '<div style="margin-top:8px; font-size:11.5px; color:var(--alert-nominal);">✔ Route actively bypasses high-risk hazard zones.</div>' : ''}
        </div>
      `;

      if (sumName) sumName.textContent = `${fromZone} ➔ ${s.name}`;
      if (sumMet) sumMet.textContent = `${sData.distance_km} km • ~${sData.duration_min} min • ${sData.congestion_level} Traffic`;

      // Plot on Map
      if (evacMap && evacLayerGroup) {
        const originCoords = sData.from_coords || (sData.path && sData.path[0]) || [12.9716, 77.5946];
        const shelterCoords = sData.shelter_coords || [s.lat, s.lon];
        const path = (sData.path && sData.path.length) ? sData.path : [originCoords, shelterCoords];

        // Origin Marker (Blue)
        const originMarker = L.circleMarker(originCoords, {
          radius: 11,
          fillColor: "#3B82F6",
          color: "#FFFFFF",
          weight: 2.5,
          opacity: 1,
          fillOpacity: 0.95
        }).addTo(evacLayerGroup);
        originMarker.bindPopup(`
          <div style="font-family:sans-serif; min-width:160px; color:#0F172A;">
            <b style="color:#2563EB; font-size:13px;">📍 Origin: ${fromZone}</b><br>
            <div style="font-size:12px; color:#475569; margin-top:3px;">Your current starting district</div>
          </div>
        `).openPopup();
        bounds.push(originCoords);

        // Safe Shelter Marker (Emerald Green)
        const shelterMarker = L.circleMarker(shelterCoords, {
          radius: 13,
          fillColor: "#10B981",
          color: "#FFFFFF",
          weight: 3,
          opacity: 1,
          fillOpacity: 0.95
        }).addTo(evacLayerGroup);
        shelterMarker.bindPopup(`
          <div style="font-family:sans-serif; min-width:200px; color:#0F172A;">
            <b style="color:#059669; font-size:14px;">🏠 ${s.name}</b><br>
            <div style="font-size:12px; color:#334155; margin:3px 0;">Safe Relief Shelter &bull; <b>${s.zone}</b></div>
            <div style="font-size:12px; color:#475569;">Capacity: <b>${s.current_occupancy} / ${s.capacity} (${occPct}%)</b></div>
            <div style="font-size:12px; color:#475569;">Helpline: <b>${s.contact || 'Disaster Control'}</b></div>
            <div style="margin-top:6px; padding:3px 6px; background:#ECFDF5; border:1px solid #10B981; border-radius:4px; font-size:11px; color:#065F46; font-weight:600;">PRIMARY SAFE HAVEN</div>
          </div>
        `);
        bounds.push(shelterCoords);

        // Safe Route Polyline
        const routeLine = L.polyline(path, {
          color: "#10B981",
          weight: 5,
          opacity: 0.9,
          lineJoin: "round"
        }).addTo(evacLayerGroup);
        routeLine.bindTooltip(`Safe Evacuation Corridor: ${sData.distance_km} km (${sData.duration_min} min)`, {
          sticky: true
        });
        path.forEach(pt => bounds.push(pt));
      }
    } else {
      shelterBox.innerHTML = `<div class="empty-state">No relief shelters currently designated in this sector.</div>`;
    }
  } catch (e) {
    console.error("Shelter routing error:", e);
    shelterBox.innerHTML = `<div class="empty-state">Could not compute shelter route. Please try again.</div>`;
  }

  // 2. Fetch District Evacuation Corridor
  try {
    const route = await getJSON(`/api/route?from=${encodeURIComponent(fromZone)}`);
    corridorBox.innerHTML = `
      <div class="risk-card-lg risk-low" style="margin-bottom:12px;">
        <div>
          <div class="zone-name">Transit toward ${route.to}</div>
          <div class="zone-detail">${route.distance_km} km &bull; Approx. ${route.duration_min} minutes</div>
        </div>
        <div class="risk-word">Clear</div>
      </div>
      <p style="color:var(--fog-300); font-size:13px; line-height:1.5;">
        ${route.to} represents the lowest hazard level and optimal traffic corridor connecting out of ${fromZone}.
        ${route.hazard_avoidance ? 'Hazard avoidance navigation algorithms have routed around known storm/flood centers.' : ''}
      </p>
    `;
  } catch (e) {
    corridorBox.innerHTML = `<div class="empty-state">Could not compute transit corridor.</div>`;
  }

  // 3. Fetch Nearby Safe Hospitals & Plot on Map
  try {
    const hospitals = await getJSON(`/api/hospitals?zone=${encodeURIComponent(fromZone)}`);
    hospGrid.innerHTML = "";
    if (hospitals && hospitals.length) {
      hospitals.slice(0, 3).forEach((h) => {
        const isSafe = h.accessibility_status === "Operational";
        hospGrid.appendChild(
          el(`
            <div class="fleet-tile" style="text-align:left; padding:14px; border:1px solid ${isSafe ? 'var(--border-subtle)' : 'var(--alert-warning)'};">
              <div style="display:flex; justify-content:space-between; align-items:flex-start; margin-bottom:6px;">
                <strong style="font-size:14px;">🏥 ${h.hospital_name}</strong>
                <span class="badge ${isSafe ? 'badge-available' : 'badge-medium'}">${h.accessibility_status || 'Operational'}</span>
              </div>
              <div class="zone-detail" style="font-size:12px; margin-bottom:6px;">${h.location} &bull; ${h.distance_km} km away</div>
              <div style="display:flex; justify-content:space-between; font-size:12px; color:var(--fog-300);" class="mono">
                <span>Beds Available: <b style="color:#FFF;">${h.beds_available}</b> / ${h.beds_total}</span>
                <span>ICU: <b style="color:#FFF;">${h.icu_available}</b></span>
              </div>
            </div>
          `)
        );

        // Plot hospital marker on map
        if (evacMap && evacLayerGroup && h.lat && h.lon) {
          const hospMarker = L.circleMarker([h.lat, h.lon], {
            radius: 8,
            fillColor: "#EC4899",
            color: "#FFFFFF",
            weight: 2,
            opacity: 1,
            fillOpacity: 0.9
          }).addTo(evacLayerGroup);
          hospMarker.bindPopup(`
            <div style="font-family:sans-serif; min-width:180px; color:#0F172A;">
              <b style="color:#DB2777; font-size:13px;">🏥 ${h.hospital_name}</b><br>
              <div style="font-size:12px; color:#334155; margin:2px 0;">${h.location} &bull; ${h.distance_km} km</div>
              <div style="font-size:11.5px; color:#475569;">Beds: <b>${h.beds_available}/${h.beds_total}</b> | ICU: <b>${h.icu_available}</b></div>
              <div style="font-size:11.5px; color:#475569;">Contact: <b>${h.contact || '108'}</b></div>
            </div>
          `);
          bounds.push([h.lat, h.lon]);
        }
      });
    } else {
      hospGrid.innerHTML = `<div class="empty-state">No hospital facilities returned.</div>`;
    }
  } catch (e) {
    hospGrid.innerHTML = `<div class="empty-state">Failed to load medical facility status.</div>`;
  }

  // 4. Plot Hazard / High Risk Warning Areas
  try {
    const pData = await getJSON("/api/predictions");
    if (pData && pData.predictions && evacMap && evacLayerGroup) {
      pData.predictions.forEach((p) => {
        if (p.risk_level === "High" && p.lat && p.lon && p.zone !== fromZone) {
          L.circle([p.lat, p.lon], {
            radius: 1200,
            color: "#EF4444",
            fillColor: "#EF4444",
            fillOpacity: 0.22,
            weight: 1.5,
            dashArray: "4, 6"
          }).addTo(evacLayerGroup).bindTooltip(`⚠️ High Hazard Area: ${p.zone} (${p.prediction})`, { sticky: true });
        }
      });
    }
  } catch (e) {
    // non-critical
  }

  // 5. Fit Map Bounds
  if (bounds.length > 0 && evacMap) {
    evacMap.fitBounds(L.latLngBounds(bounds), { padding: [50, 50], maxZoom: 14 });
    setTimeout(() => { evacMap.invalidateSize(); }, 250);
  }
}

document.getElementById("route-btn").addEventListener("click", findCitizenShelterAndRoute);
document.addEventListener("DOMContentLoaded", findCitizenShelterAndRoute);
