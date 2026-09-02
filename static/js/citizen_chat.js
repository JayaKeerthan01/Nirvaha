const SOURCE_LABELS = {
  claude: "Claude-assisted",
  live_data: "Live data lookup",
  general_knowledge: "General safety info, not live data",
  assistant: "",
};

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
document.querySelectorAll(".chat-suggestion").forEach((btn) => {
  btn.addEventListener("click", () => sendCitizenQuestion(btn.dataset.q));
});
