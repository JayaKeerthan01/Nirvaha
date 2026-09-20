async function refreshUserStats() {
  try {
    const data = await getJSON("/api/admin/users");
    document.getElementById("stat-active").textContent = data.stats.active_now;
    document.getElementById("stat-verified").textContent = data.stats.verified_citizens;
    document.getElementById("stat-total").textContent = data.stats.total_citizens;

    const rows = document.getElementById("citizen-rows");
    if (!data.citizens.length) {
      rows.innerHTML = `<tr><td colspan="7" class="empty-state">No community accounts yet.</td></tr>`;
      return;
    }
    rows.innerHTML = "";
    data.citizens.forEach((c) => {
      const verifiedBadge = c.email_verified
        ? `<span class="badge badge-low">Verified</span>`
        : `<span class="badge badge-medium">Pending</span>`;
      rows.appendChild(
        el(`
          <tr>
            <td>${c.name}</td>
            <td class="mono">${c.email}</td>
            <td class="mono" style="color:var(--signal-info);">${c.phone || "—"}</td>
            <td>${c.zone || "—"}</td>
            <td>${verifiedBadge}</td>
            <td class="mono">${c.last_seen || "never"}</td>
            <td class="mono">${c.created_at}</td>
          </tr>
        `)
      );
    });
  } catch (e) {
    console.error(e);
  }
}

setInterval(refreshUserStats, POLL_INTERVAL_MS);
