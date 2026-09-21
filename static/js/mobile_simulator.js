/**
 * NIRVAHA CITIZEN MOBILE PHONE SIMULATOR
 * Real-time SMS & WhatsApp Emergency Delivery Preview for Evaluators & Demos
 */

(function () {
  let activeTab = "sms";
  let unreadCount = 0;
  let sseSource = null;

  // Initial Seed Messages (Realistic Indian Citizen Device Context)
  const defaultMessages = [
    {
      id: 1,
      type: "sms",
      sender: "VM-NIRVAHA",
      time: "14:10",
      text: "[NIRVAHA] Citizen safety line active in Bangalore South. In case of flood or tree-fall, dial 112 or report on nirvaha.gov",
    },
    {
      id: 2,
      type: "whatsapp",
      sender: "Nirvaha Disaster Command ✓",
      time: "14:15",
      text: "🚨 *Automated Weather Notice*\nHeavy precipitation (55mm/h) predicted over Koramangala & Bellandur.\nAvoid outer ring road underpasses.",
    },
  ];

  let phoneMessages = JSON.parse(sessionStorage.getItem("nirvaha_phone_messages") || "null") || defaultMessages;

  function initMobileSimulator() {
    // 1. Create HTML structure if not already present
    if (!document.getElementById("phone-sim-wrapper")) {
      const wrapper = document.createElement("div");
      wrapper.id = "phone-sim-wrapper";
      wrapper.innerHTML = `
        <!-- Launcher Floating Pill Button -->
        <div id="phone-sim-launcher" class="phone-sim-launcher" title="Click to view live citizen phone receiving SMS & WhatsApp alerts">
          <span style="font-size: 16px;">📱</span>
          <span>Citizen Phone Preview</span>
          <span id="phone-sim-badge" class="badge-pulse" style="display: none;">0</span>
        </div>

        <!-- Floating Smartphone Frame -->
        <div id="phone-sim-container" class="phone-sim-container">
          <div class="phone-chassis">
            <!-- Dynamic Island Notch -->
            <div class="phone-island">
              <div class="phone-camera-lens"></div>
            </div>

            <!-- Top Status Bar -->
            <div class="phone-status-bar">
              <span id="phone-clock">15:58</span>
              <div class="phone-carrier">
                <span>Nirvaha 5G</span>
                <span>• Jio</span>
                <span>🔋 98%</span>
              </div>
            </div>

            <!-- Lockscreen Dropdown Banner -->
            <div id="phone-alert-banner" class="phone-alert-banner">
              <div class="phone-banner-header">
                <span class="phone-banner-app">
                  <span id="banner-app-icon">🚨</span>
                  <span id="banner-app-name">EMERGENCY ALERT</span>
                </span>
                <span class="phone-banner-time">Now</span>
              </div>
              <div class="phone-banner-title" id="banner-title">CRITICAL FLOOD WARNING</div>
              <div class="phone-banner-body" id="banner-body">Seek higher ground or prepare for evacuation immediately.</div>
            </div>

            <!-- App Switcher Tabs -->
            <div class="phone-app-tabs">
              <button class="phone-tab-btn tab-sms active" id="tab-btn-sms" onclick="window.NirvahaPhone.switchTab('sms')">
                <span>💬</span> Messages (SMS)
              </button>
              <button class="phone-tab-btn tab-whatsapp" id="tab-btn-whatsapp" onclick="window.NirvahaPhone.switchTab('whatsapp')">
                <span>🟢</span> WhatsApp
              </button>
            </div>

            <!-- Phone Screen Viewport -->
            <div class="phone-screen-body" id="phone-screen-body">
              <!-- Messages dynamically populated -->
            </div>

            <!-- Bottom Controls & Demo Actions -->
            <div class="phone-bottom-bar">
              <button class="phone-control-btn" onclick="window.NirvahaPhone.triggerDemoAlert()" title="Simulate an instant incoming SOS broadcast to this phone">
                ⚡ Test Alert
              </button>
              <button class="phone-control-btn" onclick="window.NirvahaPhone.clearMessages()" title="Clear message history">
                🗑️ Clear
              </button>
              <button class="phone-control-btn" onclick="window.NirvahaPhone.togglePhone(false)">
                ✕ Close
              </button>
            </div>

            <!-- Home Bar Indicator -->
            <div class="phone-home-indicator"></div>
          </div>
        </div>
      `;
      document.body.appendChild(wrapper);
    }

    // Attach event listeners
    document.getElementById("phone-sim-launcher")?.addEventListener("click", () => {
      window.NirvahaPhone.togglePhone(true);
    });

    document.getElementById("open-phone-sim-btn")?.addEventListener("click", () => {
      window.NirvahaPhone.togglePhone(true);
    });

    // Start clock inside phone
    setInterval(() => {
      const clockEl = document.getElementById("phone-clock");
      if (clockEl) {
        const d = new Date();
        clockEl.textContent = d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
      }
    }, 1000);

    renderMessages();
    connectSSE();
  }

  function renderMessages() {
    const bodyEl = document.getElementById("phone-screen-body");
    if (!bodyEl) return;

    bodyEl.innerHTML = "";

    if (activeTab === "sms") {
      bodyEl.className = "phone-screen-body sms-mode";

      const header = document.createElement("div");
      header.style.textAlign = "center";
      header.style.padding = "4px 0 10px 0";
      header.style.color = "#94a3b8";
      header.style.fontSize = "10px";
      header.innerHTML = `<div>🛡️ <strong>VM-NIRVAHA</strong> • Official Disaster Gateway</div><div style="font-size:8px; opacity:0.7;">TRAI Emergency Service Route</div>`;
      bodyEl.appendChild(header);

      const smsList = phoneMessages.filter(m => m.type === "sms");
      if (smsList.length === 0) {
        bodyEl.innerHTML += `<div style="text-align:center; color:#64748b; font-size:11px; margin-top:40px;">No SMS received yet. Broadcast an alert or click <strong>Test Alert</strong> below!</div>`;
      } else {
        smsList.forEach(m => {
          const bubble = document.createElement("div");
          bubble.className = "sms-bubble";
          bubble.innerHTML = `
            <div class="sms-sender">From: ${m.sender}</div>
            <div>${escapeHtml(m.text)}</div>
            <div class="sms-time">${m.time} • Delivered</div>
          `;
          bodyEl.appendChild(bubble);
        });
      }
    } else {
      bodyEl.className = "phone-screen-body whatsapp-mode";

      const waHeader = document.createElement("div");
      waHeader.className = "wa-chat-header";
      waHeader.innerHTML = `
        <div class="wa-avatar">🚨</div>
        <div>
          <div class="wa-title">Nirvaha Disaster Command <span class="wa-badge">✓</span></div>
          <div class="wa-status">Official Emergency Verified Account</div>
        </div>
      `;
      bodyEl.appendChild(waHeader);

      const waList = phoneMessages.filter(m => m.type === "whatsapp");
      if (waList.length === 0) {
        bodyEl.innerHTML += `<div style="text-align:center; color:#64748b; font-size:11px; margin-top:40px;">No WhatsApp alerts yet. Click <strong>Test Alert</strong> below to preview!</div>`;
      } else {
        waList.forEach(m => {
          const bubble = document.createElement("div");
          bubble.className = "wa-bubble";
          bubble.innerHTML = `
            <div>${formatWhatsAppText(m.text)}</div>
            <div style="margin-top:6px;">
              <a href="tel:112" class="wa-chip-btn">📞 Call 112 (Police)</a>
              <a href="tel:108" class="wa-chip-btn">🚑 Call 108 (Ambulance)</a>
              <a href="/citizen/evacuation" class="wa-chip-btn">📍 Safe Routes</a>
            </div>
            <div class="wa-bubble-time">${m.time} <span class="wa-checks">✓✓</span></div>
          `;
          bodyEl.appendChild(bubble);
        });
      }
    }

    bodyEl.scrollTop = bodyEl.scrollHeight;
  }

  function receiveAlert(payload) {
    const timeStr = new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    const title = payload.title || "Emergency Alert";
    const text = payload.message || payload.title || "Evacuate danger zone immediately.";
    const zone = payload.zone || "Bangalore South";

    // 1. Create SMS record
    const smsMsg = {
      id: Date.now(),
      type: "sms",
      sender: "VM-NIRVAHA",
      time: timeStr,
      text: payload.sms_text || `[NIRVAHA ALERT] ${title}: ${text} (${zone}) - Follow safety guidelines.`,
    };

    // 2. Create WhatsApp record
    const waMsg = {
      id: Date.now() + 1,
      type: "whatsapp",
      sender: "Nirvaha Disaster Command ✓",
      time: timeStr,
      text: payload.whatsapp_text || `🚨 *${title.toUpperCase()}*\n\n${text}\n\n📍 *Zone:* ${zone}\n⚠️ Immediate action required.`,
    };

    phoneMessages.push(smsMsg, waMsg);
    sessionStorage.setItem("nirvaha_phone_messages", JSON.stringify(phoneMessages));

    // Vibrate phone
    const container = document.getElementById("phone-sim-container");
    if (container) {
      container.classList.remove("vibrating");
      void container.offsetWidth; // trigger reflow
      container.classList.add("vibrating");
    }

    // Play alert sound if enabled
    try {
      const chime = new Audio("data:audio/wav;base64,UklGRl9vT19XQVZFZm10IBAAAAABAAEAQB8AAEAfAAABAAgAZGF0YU...==");
      chime.volume = 0.3;
      chime.play().catch(() => {});
    } catch (e) {}

    // Show Dropdown Banner
    showBanner(title, text, activeTab === "whatsapp" ? "🟢 WHATSAPP ALERT" : "🚨 EMERGENCY SMS");

    // If phone is closed, increment badge
    if (!container || !container.classList.contains("active")) {
      unreadCount += 1;
      const badge = document.getElementById("phone-sim-badge");
      if (badge) {
        badge.textContent = unreadCount;
        badge.style.display = "inline-block";
      }
      const topbarBadge = document.getElementById("topbar-phone-badge");
      if (topbarBadge) {
        topbarBadge.textContent = unreadCount;
        topbarBadge.style.display = "inline-block";
      }
    }

    renderMessages();
  }

  function showBanner(title, body, appLabel) {
    const banner = document.getElementById("phone-alert-banner");
    if (!banner) return;

    document.getElementById("banner-app-name").textContent = appLabel;
    document.getElementById("banner-title").textContent = title;
    document.getElementById("banner-body").textContent = body;

    banner.classList.add("show");
    setTimeout(() => {
      banner.classList.remove("show");
    }, 6000);
  }

  function connectSSE() {
    if (sseSource) return;
    try {
      sseSource = new EventSource("/api/notifications/stream");
      sseSource.addEventListener("emergency_broadcast", (e) => {
        try {
          const d = JSON.parse(e.data);
          receiveAlert(d);
          window.dispatchEvent(new CustomEvent("nirvaha:emergency_broadcast", { detail: d }));
        } catch (err) {}
      });
      sseSource.addEventListener("high_risk_alert", (e) => {
        try {
          const d = JSON.parse(e.data);
          receiveAlert(d);
          window.dispatchEvent(new CustomEvent("nirvaha:high_risk_alert", { detail: d }));
        } catch (err) {}
      });
      sseSource.addEventListener("whatsapp_channel_joined", (e) => {
        try {
          const d = JSON.parse(e.data);
          receiveAlert({
            title: "WhatsApp Channel Enrolled",
            message: d.message || "Enrolled in Nirvaha Official Emergency Broadcast Channel",
            whatsapp_text: d.message,
            sms_text: `[NIRVAHA] Welcome ${d.name || ""}! Your number ${d.phone || ""} has been enrolled in the Official WhatsApp Disaster Broadcast Channel.`,
            zone: d.zone,
            is_welcome: true,
          });
          switchTab("whatsapp");
        } catch (err) {}
      });

      // Free browser HTTP/1.1 socket immediately on tab navigation
      window.addEventListener("beforeunload", () => {
        if (sseSource) {
          try { sseSource.close(); } catch (err) {}
          sseSource = null;
        }
      });
    } catch (err) {}
  }

  function escapeHtml(str) {
    const div = document.createElement("div");
    div.textContent = str;
    return div.innerHTML;
  }

  function formatWhatsAppText(str) {
    let esc = escapeHtml(str);
    // Format *bold*
    esc = esc.replace(/\*([^\*]+)\*/g, "<strong>$1</strong>");
    // Format newlines
    esc = esc.replace(/\n/g, "<br>");
    return esc;
  }

  // Public API
  window.NirvahaPhone = {
    togglePhone(forceState) {
      const container = document.getElementById("phone-sim-container");
      const launcher = document.getElementById("phone-sim-launcher");
      if (!container) return;

      const shouldOpen = forceState !== undefined ? forceState : !container.classList.contains("active");
      if (shouldOpen) {
        container.classList.add("active");
        if (launcher) launcher.style.display = "none";
        unreadCount = 0;
        const badge = document.getElementById("phone-sim-badge");
        if (badge) badge.style.display = "none";
        renderMessages();
      } else {
        container.classList.remove("active");
        if (launcher) launcher.style.display = "flex";
      }
    },

    switchTab(tab) {
      activeTab = tab;
      document.getElementById("tab-btn-sms")?.classList.toggle("active", tab === "sms");
      document.getElementById("tab-btn-whatsapp")?.classList.toggle("active", tab === "whatsapp");
      renderMessages();
    },

    triggerDemoAlert() {
      receiveAlert({
        title: "CRITICAL: Severe Flood & Road Breach",
        message: "Koramangala 4th Block & 100ft Road submerged. Water depth > 3.5ft. Evacuate to St. John's Relief Shelter now. Emergency Units en route.",
        zone: "Koramangala, Bangalore",
        sms_text: "[NIRVAHA SOS] Koramangala 4th Block: Severe Flooding. Water > 3.5ft. Evacuate to St. John's Relief Shelter immediately.",
        whatsapp_text: "🚨 *CRITICAL FLOOD ALERT: KORAMANGALA*\n\nSevere waterlogging reported. Outer Ring Road closed.\n\nEvacuation Point: *St. John's Relief Camp*\nHotlines: 112 (Police) | 108 (Ambulance)",
      });
    },

    clearMessages() {
      phoneMessages = [];
      sessionStorage.removeItem("nirvaha_phone_messages");
      renderMessages();
    },

    receive(payload) {
      receiveAlert(payload);
    }
  };

  // Initialize once DOM is ready
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initMobileSimulator);
  } else {
    initMobileSimulator();
  }
})();
