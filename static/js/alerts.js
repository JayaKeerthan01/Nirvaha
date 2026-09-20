function severityIconBg(sev) {
  return { Low: "var(--alert-nominal-dim)", Medium: "var(--alert-warning-dim)", High: "var(--alert-critical-dim)" }[sev] || "var(--signal-info-dim)";
}
function severityIconColor(sev) {
  return { Low: "#2DD4BF", Medium: "#FFB238", High: "#FF5470" }[sev] || "#5EA8FF";
}

function channelBadge(ch) {
  const map = {
    email: '<span class="badge badge-info" style="font-size:11px;">EMAIL</span>',
    sms: '<span class="badge badge-medium" style="font-size:11px;">SMS</span>',
    push: '<span class="badge badge-available" style="font-size:11px;">PUSH</span>',
  };
  return map[ch] || `<span class="badge badge-info">${ch}</span>`;
}

function deliveryStatusBadge(status) {
  if (status === "sent") return '<span class="badge badge-available">Sent</span>';
  if (status === "simulated") return '<span class="badge badge-info" title="Zero-config simulated transmission recorded">Simulated</span>';
  return '<span class="badge badge-high">Failed</span>';
}

async function renderNotifications() {
  const tbody = document.getElementById("notification-rows");
  const countBadge = document.getElementById("notif-count-badge");
  if (!tbody) return;

  try {
    const data = await getJSON("/api/notifications");
    const history = data.history || [];
    if (countBadge) countBadge.textContent = `${history.length} logged`;

    if (!history.length) {
      tbody.innerHTML = `<tr><td colspan="6" class="empty-state">No outbound SMS or Email alerts logged yet. Alerts trigger automatically when an area reaches High risk.</td></tr>`;
      return;
    }

    tbody.innerHTML = "";
    history.forEach((n) => {
      tbody.appendChild(
        el(`
          <tr>
            <td class="mono" style="font-size:11.5px;">${fmtTime(n.created_at)}</td>
            <td>${channelBadge(n.channel)}</td>
            <td class="mono" style="font-size:12px; font-weight:600;">${n.recipient}</td>
            <td>${n.zone || '<span class="zone-meta">City-wide</span>'}</td>
            <td>
              <div style="font-weight:600; font-size:12.5px;">${n.title}</div>
              <div class="zone-meta" style="max-width:460px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis;">${n.message}</div>
            </td>
            <td>${deliveryStatusBadge(n.status)}</td>
          </tr>
        `)
      );
    });
  } catch (e) {
    console.error("Failed to load notifications:", e);
  }
}

async function renderAlerts() {
  const container = document.getElementById("alerts-list");
  try {
    const alerts = await getJSON("/api/alerts");
    if (!alerts.length) {
      container.innerHTML = `<div class="empty-state">No alerts logged yet. The Coordinator Agent raises one automatically whenever a zone's risk reaches High.</div>`;
      return;
    }
    container.innerHTML = "";
    alerts.forEach((a) => {
      container.appendChild(
        el(`
          <div class="alert-item">
            <div class="alert-icon" style="background:${severityIconBg(a.severity)}; color:${severityIconColor(a.severity)};">⚠</div>
            <div style="flex:1;">
              <div class="alert-title">${a.title}</div>
              <div class="alert-message">${a.message}</div>
              <div class="alert-time">${fmtTime(a.created_at)} · zone: ${a.zone || "—"}</div>
            </div>
            <span class="badge ${riskClass(a.severity)}">${a.severity}</span>
          </div>
        `)
      );
    });
  } catch (e) {
    console.error(e);
  }
}

async function renderIncidents() {
  const rows = document.getElementById("incident-rows");
  try {
    const incidents = await getJSON("/api/incidents");
    if (!incidents.length) {
      rows.innerHTML = `<tr><td colspan="5" class="empty-state">No assessments logged yet.</td></tr>`;
      return;
    }
    rows.innerHTML = "";
    incidents.forEach((i) => {
      rows.appendChild(
        el(`
          <tr>
            <td class="mono">${fmtTime(i.date)}</td>
            <td>${i.location}</td>
            <td>${i.disaster_type}</td>
            <td><span class="badge ${riskClass(i.severity)}">${i.severity}</span></td>
            <td class="mono">${(i.probability * 100).toFixed(1)}%</td>
          </tr>
        `)
      );
    });
  } catch (e) {
    console.error(e);
  }
}

async function initPageBroadcast() {
  const form = document.getElementById("page-broadcast-form");
  const zoneSelect = document.getElementById("p-bc-zone");
  const submitBtn = document.getElementById("p-bc-submit-btn");

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
    console.warn("Could not load zones:", e);
  }

  if (form) {
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      const title = (document.getElementById("p-bc-title").value || "").trim();
      const zone = document.getElementById("p-bc-zone").value || null;
      const message = (document.getElementById("p-bc-msg").value || "").trim();

      const channels = [];
      if (document.getElementById("p-bc-push")?.checked) channels.push("push");
      if (document.getElementById("p-bc-sms")?.checked) channels.push("sms");
      if (document.getElementById("p-bc-whatsapp")?.checked) channels.push("whatsapp");
      if (document.getElementById("p-bc-email")?.checked) channels.push("email");

      if (!title || !message) return;

      submitBtn.disabled = true;
      submitBtn.textContent = "Broadcasting…";

      try {
        await postJSON("/api/notifications/broadcast", { title, zone, message, channels });
        alert(`Broadcast successfully dispatched across ${channels.join(", ").toUpperCase()}`);
        document.getElementById("p-bc-title").value = "";
        document.getElementById("p-bc-msg").value = "";
        renderNotifications();
        renderAlerts();
      } catch (err) {
        alert("Broadcast dispatch failed: " + err.message);
      } finally {
        submitBtn.disabled = false;
        submitBtn.textContent = "Dispatch Broadcast";
      }
    });
  }
}

document.addEventListener("DOMContentLoaded", () => {
  renderNotifications();
  renderAlerts();
  renderIncidents();
  initPageBroadcast();
  setInterval(() => {
    renderNotifications();
    renderAlerts();
    renderIncidents();
  }, POLL_INTERVAL_MS);
});
