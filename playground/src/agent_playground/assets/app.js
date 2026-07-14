const state = {
  mode: "agent",
  banks: [],
  messages: [],
  sessionId: null,
  parentEntryId: null,
  runId: null,
  prepared: null,
  memory: null,
  tools: [],
  tab: "memory",
};

const elements = {
  serviceStatus: document.querySelector("#service-status"),
  newChat: document.querySelector("#new-chat"),
  bank: document.querySelector("#memory-bank"),
  memoryQuery: document.querySelector("#memory-query"),
  previewMemory: document.querySelector("#preview-memory"),
  context: document.querySelector("#pasted-context"),
  systemPrompt: document.querySelector("#system-prompt"),
  transcript: document.querySelector("#transcript"),
  emptyChat: document.querySelector("#empty-chat"),
  composer: document.querySelector("#composer"),
  prompt: document.querySelector("#prompt"),
  send: document.querySelector("#send"),
  stop: document.querySelector("#stop-run"),
  runStatus: document.querySelector("#run-status"),
};

function node(tag, text, className) {
  const item = document.createElement(tag);
  if (text !== undefined && text !== null) item.textContent = String(text);
  if (className) item.className = className;
  return item;
}

async function requestJson(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: { accept: "application/json", ...options.headers },
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(payload.error?.message || `Request failed: ${response.status}`);
  return payload;
}

async function initialize() {
  try {
    const [config, banks] = await Promise.all([
      requestJson("/api/config"),
      requestJson("/api/banks"),
    ]);
    state.banks = banks.items || [];
    elements.systemPrompt.value = config.defaultSystemPrompt || "";
    renderBanks();
    elements.serviceStatus.textContent = "Connected";
  } catch {
    elements.serviceStatus.textContent = "Services unavailable";
  }
  renderMode();
  renderInspector();
}

function renderBanks() {
  const current = elements.bank.value;
  elements.bank.replaceChildren(node("option", "Memory off"));
  elements.bank.firstChild.value = "";
  for (const bank of state.banks) {
    const option = node("option", bank.name || bank.bank_id);
    option.value = bank.bank_id;
    elements.bank.append(option);
  }
  if ([...elements.bank.options].some((option) => option.value === current)) {
    elements.bank.value = current;
  }
}

function renderMode() {
  document.querySelectorAll("[data-mode]").forEach((button) => {
    button.setAttribute("aria-pressed", String(button.dataset.mode === state.mode));
  });
}

function addMessage(role, text = "") {
  const message = { role, text };
  state.messages.push(message);
  const row = node("article", null, `message ${role}`);
  row.append(node("div", role === "user" ? "You" : state.mode === "agent" ? "Pi Agent" : "LLM", "message-author"));
  const body = node("pre", text, "message-body");
  row.append(body);
  elements.transcript.append(row);
  elements.emptyChat.hidden = true;
  elements.transcript.scrollTop = elements.transcript.scrollHeight;
  return { message, body };
}

function recentConversation() {
  return state.messages
    .slice(-8)
    .map((message) => `${message.role}: ${message.text}`)
    .join("\n")
    .slice(0, 8000);
}

function setRunning(running, text) {
  elements.send.disabled = running;
  elements.newChat.disabled = running;
  document.querySelectorAll("[data-mode]").forEach((button) => {
    button.disabled = running;
  });
  elements.stop.hidden = !running;
  elements.runStatus.textContent = text;
}

async function runPrompt(prompt) {
  const recallContext = recentConversation();
  addMessage("user", prompt);
  const assistant = addMessage("assistant");
  state.tools = [];
  state.prepared = null;
  state.memory = null;
  renderInspector();
  setRunning(true, "Preparing");
  try {
    const response = await fetch("/api/runs", {
      method: "POST",
      headers: { "content-type": "application/json", accept: "application/x-ndjson" },
      body: JSON.stringify({
        mode: state.mode,
        prompt,
        bankId: elements.bank.value || null,
        memoryQuery: elements.memoryQuery.value.trim() || null,
        recallContext,
        context: elements.context.value,
        systemPrompt: elements.systemPrompt.value,
        sessionId: state.sessionId,
        parentEntryId: state.parentEntryId,
      }),
    });
    if (!response.ok) {
      const payload = await response.json().catch(() => ({}));
      throw new Error(payload.error?.message || `Run failed: ${response.status}`);
    }
    await readEvents(response, (event) => handleEvent(event, assistant));
  } catch (error) {
    assistant.message.text = `Run failed: ${error.message}`;
    assistant.body.textContent = assistant.message.text;
    setRunning(false, "Failed");
  } finally {
    state.runId = null;
    setRunning(false, elements.runStatus.textContent);
  }
}

async function readEvents(response, onEvent) {
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    const { value, done } = await reader.read();
    buffer += decoder.decode(value || new Uint8Array(), { stream: !done });
    let newline;
    while ((newline = buffer.indexOf("\n")) >= 0) {
      const line = buffer.slice(0, newline).trim();
      buffer = buffer.slice(newline + 1);
      if (line) onEvent(JSON.parse(line));
    }
    if (done) break;
  }
  if (buffer.trim()) onEvent(JSON.parse(buffer));
}

function handleEvent(event, assistant) {
  if (event.type === "run_prepared") {
    state.runId = event.runId;
    state.prepared = event;
    state.memory = event.memory;
    setRunning(true, "Running");
    renderInspector();
    return;
  }
  if (event.type === "run_started") return;
  if (event.type === "tool_snapshot") {
    state.tools.push(event);
    setRunning(true, event.summary || "Using tool");
    renderInspector();
    return;
  }
  if (event.type === "text_delta") {
    assistant.message.text = event.reset ? event.delta : assistant.message.text + event.delta;
    assistant.body.textContent = assistant.message.text;
    elements.transcript.scrollTop = elements.transcript.scrollHeight;
    return;
  }
  if (event.type === "run_completed") {
    assistant.message.text = event.answer;
    assistant.body.textContent = event.answer;
    state.sessionId = event.sessionId;
    state.parentEntryId = event.entryId;
    setRunning(false, "Complete");
    return;
  }
  if (event.type === "run_failed") {
    assistant.message.text = event.message || "Agent run failed";
    assistant.body.textContent = assistant.message.text;
    setRunning(false, event.code === "CANCELLED" ? "Cancelled" : "Failed");
  }
}

async function previewMemory() {
  const bankId = elements.bank.value;
  const query = elements.memoryQuery.value.trim() || elements.prompt.value.trim();
  if (!bankId || !query) {
    elements.runStatus.textContent = "Select a bank and enter a query";
    return;
  }
  elements.runStatus.textContent = "Recalling";
  try {
    state.memory = await requestJson("/api/recall", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ bankId, query }),
    });
    state.tab = "memory";
    elements.runStatus.textContent = "Recall complete";
    renderInspector();
  } catch (error) {
    elements.runStatus.textContent = error.message;
  }
}

function renderInspector() {
  document.querySelectorAll("[role=tab]").forEach((button) => {
    button.setAttribute("aria-selected", String(button.dataset.tab === state.tab));
  });
  for (const tab of ["memory", "request", "tools"]) {
    document.querySelector(`#inspector-${tab}`).hidden = tab !== state.tab;
  }
  renderMemory();
  renderRequest();
  renderTools();
}

function renderMemory() {
  const panel = document.querySelector("#inspector-memory");
  if (!state.memory) {
    panel.replaceChildren(node("p", "No memory recall for this run.", "empty-inspector"));
    return;
  }
  const blocks = [
    field("Bank", state.memory.bankId),
    field("Recall query", state.memory.query),
  ];
  const memories = Array.isArray(state.memory.memories) ? state.memory.memories : [];
  if (memories.length === 0) blocks.push(node("p", "No matching memories.", "empty-inspector"));
  for (const memory of memories) {
    const item = node("section", null, "memory-item");
    item.append(node("div", memory.type || "memory", "memory-type"));
    item.append(node("p", memory.text, "memory-text"));
    item.append(node("code", memory.id, "memory-id"));
    blocks.push(item);
  }
  panel.replaceChildren(...blocks);
}

function renderRequest() {
  const panel = document.querySelector("#inspector-request");
  if (!state.prepared) {
    panel.replaceChildren(node("p", "No prepared request yet.", "empty-inspector"));
    return;
  }
  panel.replaceChildren(
    field("Mode", `${state.prepared.mode} / ${state.prepared.toolPolicy}`),
    field("Run ID", state.prepared.runId),
    field("System prompt", state.prepared.request.systemPrompt),
    field("Current request", state.prepared.request.prompt),
    field("Injected context", JSON.stringify(state.prepared.request.context, null, 2)),
  );
}

function renderTools() {
  const panel = document.querySelector("#inspector-tools");
  if (state.tools.length === 0) {
    panel.replaceChildren(node("p", "No tool activity.", "empty-inspector"));
    return;
  }
  panel.replaceChildren(...state.tools.map((tool) => {
    const row = node("div", null, "tool-row");
    row.append(node("span", tool.tool || "tool", "tool-name"));
    row.append(node("span", tool.summary || tool.phase || "", "tool-summary"));
    return row;
  }));
}

function field(label, value) {
  const item = node("section", null, "inspect-field");
  item.append(node("h3", label));
  item.append(node("pre", value || "None"));
  return item;
}

function newChat() {
  state.messages = [];
  state.sessionId = null;
  state.parentEntryId = null;
  state.runId = null;
  state.prepared = null;
  state.memory = null;
  state.tools = [];
  elements.transcript.querySelectorAll(".message").forEach((message) => message.remove());
  elements.emptyChat.hidden = false;
  elements.prompt.value = "";
  setRunning(false, "Ready");
  renderInspector();
  elements.prompt.focus();
}

document.querySelectorAll("[data-mode]").forEach((button) => {
  button.addEventListener("click", () => {
    if (button.dataset.mode !== state.mode) {
      state.mode = button.dataset.mode;
      newChat();
      renderMode();
    }
  });
});

document.querySelectorAll("[role=tab]").forEach((button) => {
  button.addEventListener("click", () => {
    state.tab = button.dataset.tab;
    renderInspector();
  });
});

elements.composer.addEventListener("submit", (event) => {
  event.preventDefault();
  const prompt = elements.prompt.value.trim();
  if (!prompt || state.runId) return;
  elements.prompt.value = "";
  void runPrompt(prompt);
});
elements.prompt.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    elements.composer.requestSubmit();
  }
});
elements.previewMemory.addEventListener("click", () => void previewMemory());
elements.newChat.addEventListener("click", newChat);
elements.stop.addEventListener("click", async () => {
  if (!state.runId) return;
  elements.runStatus.textContent = "Cancelling";
  await requestJson(`/api/runs/${encodeURIComponent(state.runId)}/cancel`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: "{}",
  }).catch(() => {});
});

void initialize();
