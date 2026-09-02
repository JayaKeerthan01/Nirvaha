const SOURCE_LABELS = {
  claude: "Claude-assisted",
  live_data: "Live data lookup",
  general_knowledge: "General safety info, not live data",
  assistant: "",
};

// Rotating pool for the general-safety slots in the suggestion chips —
// picked at random each page load so returning visitors discover more of
// what the FAQ layer (agents/citizen_chat_agent.py::FAQ_TOPICS) covers,
// instead of seeing the same two chips every time.
const FAQ_SUGGESTIONS = [
  { q: "What should I pack in an emergency kit?", label: "Emergency kit checklist" },
  { q: "How do I purify water during a flood?", label: "Purifying water" },
  { q: "Cyclone safety tips", label: "Cyclone safety" },
  { q: "What do I do during a power cut?", label: "Power outage tips" },
  { q: "Landslide warning signs", label: "Landslide warning signs" },
  { q: "First aid for bleeding", label: "Basic first aid" },
];

function shuffle(arr) {
  const a = arr.slice();
  for (let i = a.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1));
    [a[i], a[j]] = [a[j], a[i]];
  }
  return a;
}

function riskWordShort(level) {
  return { Low: "low", Medium: "elevated", High: "high" }[level] || (level || "").toLowerCase();
}

// Builds the suggestion chips from what's actually going on right now:
// the person's own home zone (set at signup) if it's currently worth
// asking about, otherwise whichever zone has the highest live risk score,
// plus a couple of rotating general-safety topics so the chips aren't
// static regardless of who's asking or what's happening.
async function buildSuggestions() {
  const container = document.getElementById("chat-suggestions");
  let chips = [];

  try {
    const zones = await getJSON("/api/weather");
    if (zones.length) {
      const worst = zones.slice().sort((a, b) => b.risk_score - a.risk_score)[0];
      const homeMatch = typeof HOME_ZONE !== "undefined" && HOME_ZONE
        ? zones.find((z) => z.zone === HOME_ZONE)
        : null;
      const personalized = homeMatch || worst;

      chips.push({
        q: `What's the risk in ${personalized.zone}?`,
        label: homeMatch ? `Risk in ${personalized.zone} (your area)` : `Risk in ${personalized.zone}`,
      });

      if (worst.risk_level !== "Low" && worst.zone !== personalized.zone) {
        // Something else is more urgent than the person's own area right
        // now — surface it rather than only ever asking about "home".
        chips.push({
          q: `Why is ${worst.zone} at risk?`,
          label: `${worst.zone}: ${riskWordShort(worst.risk_level)} risk`,
        });
      } else {
        chips.push({
          q: `What's the safest evacuation route from ${personalized.zone}?`,
          label: `Evacuation from ${personalized.zone}`,
        });
      }

      chips.push({
        q: `Nearest hospital to ${personalized.zone}`,
        label: `Hospitals near ${personalized.zone}`,
      });
    }
  } catch (e) {
    console.error("Couldn't load live data for suggestions:", e);
  }

  const remaining = Math.max(0, 5 - chips.length);
  chips = chips.concat(shuffle(FAQ_SUGGESTIONS).slice(0, remaining));
  chips.push({ q: "What can you do?", label: "What can you do?" });

  container.innerHTML = "";
  chips.forEach((c) => {
    const btn = el(`<button class="chat-suggestion">${c.label}</button>`);
    btn.addEventListener("click", () => sendCitizenQuestion(c.q));
    container.appendChild(btn);
  });
}

function appendBubble(text, who, source) {
  const win = document.getElementById("chat-window");
  const label = source ? SOURCE_LABELS[source] : "";
  const sourceTag = label && who === "bot" ? `<span class="chat-source">${label}</span>` : "";
  win.appendChild(el(`<div class="chat-bubble ${who}">${text}${sourceTag}</div>`));
  win.scrollTop = win.scrollHeight;
}

async function sendCitizenQuestion(question) {
  if (!question || !question.trim()) return;
  appendBubble(escapeHtml(question), "user");
  const input = document.getElementById("chat-input");
  input.value = "";
  const sendBtn = document.getElementById("chat-send");
  sendBtn.disabled = true;

  try {
    const data = await postJSON("/api/citizen/chat", { question });
    appendBubble(escapeHtml(data.answer), "bot", data.source);
  } catch (e) {
    appendBubble("Sorry, something went wrong answering that. Please try again.", "bot");
  } finally {
    sendBtn.disabled = false;
    input.focus();
  }
}

function escapeHtml(s) {
  const div = document.createElement("div");
  div.textContent = s;
  return div.innerHTML;
}

document.getElementById("chat-send").addEventListener("click", () => {
  sendCitizenQuestion(document.getElementById("chat-input").value);
});
document.getElementById("chat-input").addEventListener("keydown", (e) => {
  if (e.key === "Enter") sendCitizenQuestion(document.getElementById("chat-input").value);
});

buildSuggestions();
