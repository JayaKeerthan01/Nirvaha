let tMap, tZoneLayer, tRouteLayer;
let trafficMapReady = false;
let trafficMarkers = {};      // zone name -> Leaflet circleMarker, kept across polls
let tBoundsFittedForCount = 0;

function initTrafficMap() {
  try {
    if (typeof L === "undefined") throw new Error("Leaflet failed to load");
    tMap = L.map("traffic-map", {
      attributionControl: false,
      zoomAnimation: true,
      fadeAnimation: true,
    }).setView([12.9121, 77.6446], 12);
    L.tileLayer("https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png", { maxZoom: 18 }).addTo(tMap);
    tZoneLayer = L.layerGroup().addTo(tMap);
    tRouteLayer = L.layerGroup().addTo(tMap);
    trafficMapReady = true;
  } catch (e) {
    console.warn("Traffic map unavailable:", e.message);
    const el = document.getElementById("traffic-map");
    if (el) el.innerHTML = '<div class="empty-state">Map tiles unavailable — check browser internet access.</div>';
  }
}

function congestionRadius(level) {
  return { Low: 8, Moderate: 11, High: 15, Severe: 19 }[level] || 8;
}

async function renderTrafficTable() {
  const rows = document.getElementById("traffic-rows");
  try {
    const data = await getJSON("/api/traffic");
    rows.innerHTML = "";

    const seen = new Set();
    data.forEach((t) => {
      rows.appendChild(
        el(`
          <tr>
            <td>${t.zone}</td>
            <td><span class="badge ${congestionClass(t.congestion_level)}">${t.congestion_level}</span></td>
            <td>${t.road_status}</td>
            <td class="mono">${Math.round(t.confidence * 100)}%</td>
          </tr>
        `)
      );

      if (trafficMapReady) {
        seen.add(t.zone);
        const color = { Low: "#2DD4BF", Moderate: "#FFB238", High: "#f97316", Severe: "#FF5470" }[t.congestion_level];
        const radius = congestionRadius(t.congestion_level);
        const popup = `<b>${t.zone}</b><br>${t.road_status} (${Math.round(t.confidence * 100)}% confidence)`;
        let marker = trafficMarkers[t.zone];
        if (!marker) {
          marker = L.circleMarker([t.lat, t.lon], { radius, color, fillColor: color, fillOpacity: 0.4, weight: 2 })
            .bindPopup(popup)
            .addTo(tZoneLayer);
          trafficMarkers[t.zone] = marker;
        } else {
          // Update in place instead of clear+recreate — smooth color/size
          // transition (see .leaflet-interactive in style.css) rather than
          // every zone marker flashing on every 15s poll.
          marker.setStyle({ radius, color, fillColor: color });
          marker.setLatLng([t.lat, t.lon]);
          marker.setPopupContent(popup);
        }
      }
    });

    if (trafficMapReady) {
      Object.keys(trafficMarkers).forEach((zone) => {
        if (!seen.has(zone)) {
          tZoneLayer.removeLayer(trafficMarkers[zone]);
          delete trafficMarkers[zone];
        }
      });
      if (data.length > 0 && data.length !== tBoundsFittedForCount) {
        const bounds = L.latLngBounds(data.map((t) => [t.lat, t.lon]));
        tMap.flyToBounds(bounds, { padding: [40, 40], maxZoom: 14, duration: 0.8 });
        tBoundsFittedForCount = data.length;
      }
    }
  } catch (e) {
    console.error(e);
  }
}

async function findRoute() {
  const fromZone = document.getElementById("from-zone").value;
  const resultBox = document.getElementById("route-result");
  resultBox.innerHTML = `<div class="empty-state">Calculating…</div>`;
  try {
    const route = await getJSON(`/api/route?from=${encodeURIComponent(fromZone)}`);
    resultBox.innerHTML = `
      <div class="zone-row">
        <div>
          <div class="zone-name">${route.from} → ${route.to}</div>
          <div class="zone-meta">${route.distance_km} km · est. ${route.duration_min} min · source: ${route.source}</div>
        </div>
        <span class="badge badge-info">Recommended</span>
      </div>
    `;
    if (trafficMapReady && route.path) {
      tRouteLayer.clearLayers();
      const line = L.polyline(route.path, { color: "#5EA8FF", weight: 4 }).addTo(tRouteLayer);
      tMap.fitBounds(line.getBounds(), { padding: [30, 30] });
    }
  } catch (e) {
    resultBox.innerHTML = `<div class="empty-state">Could not compute a route.</div>`;
  }
}

initTrafficMap();
renderTrafficTable();
setInterval(renderTrafficTable, POLL_INTERVAL_MS);
document.getElementById("route-btn").addEventListener("click", findRoute);
