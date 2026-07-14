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
  view: "playground",
  sessions: [],
  sessionTotal: 0,
  sessionCursor: null,
  sessionQuery: "",
  selectedSessionId: null,
  sessionDetail: null,
  audits: [],
  auditTotal: 0,
  selectedAuditId: null,
  auditDetail: null,
  historyLoading: false,
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
  playgroundView: document.querySelector("#playground-view"),
  historyView: document.querySelector("#history-view"),
  refreshHistory: document.querySelector("#refresh-history"),
  resumeBanner: document.querySelector("#resume-banner"),
  resumeText: document.querySelector("#resume-text"),
  clearResume: document.querySelector("#clear-resume"),
  sessionSearch: document.querySelector("#session-search"),
  sessionQuery: document.querySelector("#session-query"),
  sessionCount: document.querySelector("#session-count"),
  sessionList: document.querySelector("#session-list"),
  loadMoreSessions: document.querySelector("#load-more-sessions"),
  sessionTitle: document.querySelector("#session-title"),
  sessionMeta: document.querySelector("#session-meta"),
  sessionTree: document.querySelector("#session-tree"),
  continueLeaf: document.querySelector("#continue-leaf"),
  auditCount: document.querySelector("#audit-count"),
  auditSelect: document.querySelector("#audit-select"),
  auditSummary: document.querySelector("#audit-summary"),
  auditEvents: document.querySelector("#audit-events"),
};

function node(tag, text, className) {
  const item = document.createElement(tag);
  if (text !== undefined && text !== null) item.textContent = String(text);
  if (className) item.className = className;
  return item;
}

function button(text, className, onClick) {
  const item = node("button", text, className);
  item.type = "button";
  item.addEventListener("click", onClick);
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
  renderView();
  renderResume();
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
  document.querySelectorAll("[data-view]").forEach((button) => {
    button.disabled = running;
  });
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
  if (event.type === "memory_snapshot") {
    state.memory = {
      bankId: event.scopeId,
      query: event.queries.join("\n\n"),
      queries: event.queries,
      memories: event.memories,
      managedBy: "agent",
      status: "complete",
    };
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
    renderResume();
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
    field(
      Array.isArray(state.memory.queries) && state.memory.queries.length > 1
        ? "Recall queries"
        : "Recall query",
      state.memory.query,
    ),
  ];
  const memories = Array.isArray(state.memory.memories) ? state.memory.memories : [];
  if (state.memory.status === "pending") {
    blocks.push(node("p", "Agent is fetching memory...", "empty-inspector"));
  } else if (memories.length === 0) {
    blocks.push(node("p", "No matching memories.", "empty-inspector"));
  }
  for (const memory of memories) {
    const item = node("section", null, "memory-item");
    item.append(node("div", memory.type || "memory", "memory-type"));
    item.append(node("p", memory.text, "memory-text"));
    const metadata = [
      ...(memory.entities || []),
      memory.occurredStart,
      memory.documentId,
    ].filter(Boolean).join(" · ");
    if (metadata) item.append(node("div", metadata, "memory-meta"));
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
  renderResume();
  elements.prompt.focus();
}

function renderResume() {
  const continuing = Boolean(state.sessionId && state.parentEntryId);
  elements.resumeBanner.hidden = !continuing;
  elements.resumeText.textContent = continuing
    ? `Continuing ${state.sessionId} from ${state.parentEntryId}`
    : "";
}

function renderView() {
  const history = state.view === "history";
  elements.playgroundView.hidden = history;
  elements.historyView.hidden = !history;
  elements.refreshHistory.hidden = !history;
  elements.newChat.hidden = history;
  document.querySelectorAll("[data-view]").forEach((item) => {
    item.setAttribute("aria-pressed", String(item.dataset.view === state.view));
  });
}

function showView(view) {
  if (!new Set(["playground", "history"]).has(view)) return;
  state.view = view;
  renderView();
  if (view === "history" && state.sessions.length === 0 && !state.historyLoading) {
    void loadSessions();
  }
}

function formatDate(value) {
  if (!value) return "Unknown time";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleString();
}

function short(value, maximum = 180) {
  const text = String(value ?? "").replace(/<\/?current_request>/g, "").trim();
  return text.length <= maximum ? text : `${text.slice(0, maximum - 3)}...`;
}

function currentRequest(value) {
  const text = String(value ?? "");
  const match = text.match(/<current_request>\s*([\s\S]*?)\s*<\/current_request>/);
  return (match?.[1] || text).trim();
}

function pretty(value) {
  return JSON.stringify(value, null, 2) ?? "null";
}

async function loadSessions({ append = false } = {}) {
  const loadToken = (state.sessionLoadToken || 0) + 1;
  state.sessionLoadToken = loadToken;
  state.historyLoading = true;
  state.historyError = null;
  renderSessionList();
  const queryAtStart = state.sessionQuery;
  const params = new URLSearchParams({ limit: "50" });
  if (queryAtStart) params.set("q", queryAtStart);
  if (append && state.sessionCursor) params.set("cursor", state.sessionCursor);
  try {
    const page = await requestJson(`/api/sessions?${params}`);
    if (loadToken !== state.sessionLoadToken || queryAtStart !== state.sessionQuery) return;
    state.sessions = append ? [...state.sessions, ...page.items] : page.items;
    state.sessionTotal = page.total;
    state.sessionCursor = page.nextCursor;
    renderSessionList();
    if (!append) {
      const retained = state.sessions.some((item) => item.id === state.selectedSessionId);
      const nextId = retained ? state.selectedSessionId : state.sessions[0]?.id;
      if (nextId) void selectSession(nextId);
      else clearSelectedSession();
    }
  } catch (error) {
    if (loadToken !== state.sessionLoadToken) return;
    state.historyError = error.message;
    renderSessionList();
  } finally {
    if (loadToken === state.sessionLoadToken) {
      state.historyLoading = false;
      renderSessionList();
    }
  }
}

function renderSessionList() {
  elements.sessionCount.textContent = state.historyLoading
    ? "Loading"
    : `${state.sessionTotal} total`;
  elements.loadMoreSessions.hidden = !state.sessionCursor;
  elements.loadMoreSessions.disabled = state.historyLoading;
  if (state.historyError) {
    elements.sessionList.replaceChildren(node("p", state.historyError, "history-empty"));
    return;
  }
  if (state.historyLoading && state.sessions.length === 0) {
    elements.sessionList.replaceChildren(node("p", "Loading sessions...", "history-empty"));
    return;
  }
  if (state.sessions.length === 0) {
    elements.sessionList.replaceChildren(node("p", "No sessions found.", "history-empty"));
    return;
  }
  const rows = state.sessions.map((session) => {
    const row = button(null, "session-row", () => void selectSession(session.id));
    row.setAttribute("role", "listitem");
    row.setAttribute("aria-current", String(session.id === state.selectedSessionId));
    row.append(node("span", session.name || short(currentRequest(session.firstMessage), 70) || session.id, "session-row-title"));
    row.append(node("span", short(currentRequest(session.firstMessage), 150) || "Empty session", "session-row-preview"));
    row.append(node("span", `${session.messageCount} messages · ${formatDate(session.modifiedAt)}`, "session-row-meta"));
    return row;
  });
  elements.sessionList.replaceChildren(...rows);
}

function clearSelectedSession() {
  state.selectedSessionId = null;
  state.sessionDetail = null;
  state.sessionDetailError = null;
  state.audits = [];
  state.auditTotal = 0;
  state.selectedAuditId = null;
  state.auditDetail = null;
  state.auditError = null;
  renderSessionList();
  renderSessionDetail();
  renderAudits();
}

async function selectSession(sessionId) {
  state.selectedSessionId = sessionId;
  state.sessionDetail = null;
  state.sessionDetailError = null;
  state.audits = [];
  state.auditTotal = 0;
  state.selectedAuditId = null;
  state.auditDetail = null;
  state.auditError = null;
  renderSessionList();
  renderSessionDetail();
  renderAudits();
  const [detailResult, auditsResult] = await Promise.allSettled([
    requestJson(`/api/sessions/${encodeURIComponent(sessionId)}`),
    requestJson(`/api/audits?limit=100&sessionId=${encodeURIComponent(sessionId)}`),
  ]);
  if (state.selectedSessionId !== sessionId) return;
  if (detailResult.status === "fulfilled") state.sessionDetail = detailResult.value;
  else state.sessionDetailError = detailResult.reason.message;
  if (auditsResult.status === "fulfilled") {
    state.audits = auditsResult.value.items;
    state.auditTotal = auditsResult.value.total;
  } else {
    state.auditError = auditsResult.reason.message;
  }
  renderSessionDetail();
  renderAudits();
  if (state.audits.length > 0) void selectAudit(state.audits[0].runId);
}

function entryDepth(entry, byId, cache, trail = new Set()) {
  if (cache.has(entry.id)) return cache.get(entry.id);
  if (!entry.parentId || trail.has(entry.id)) return 0;
  const parent = byId.get(entry.parentId);
  if (!parent) return 0;
  const nextTrail = new Set(trail);
  nextTrail.add(entry.id);
  const depth = Math.min(32, entryDepth(parent, byId, cache, nextTrail) + 1);
  cache.set(entry.id, depth);
  return depth;
}

function contentPart(part) {
  if (!part || typeof part !== "object") {
    return node("pre", String(part ?? ""), "entry-text");
  }
  if (part.type === "text") {
    const promptParts = structuredPrompt(part.text || "");
    if (promptParts.length === 0) return node("pre", part.text || "", "entry-text");
    const block = node("div");
    block.append(...promptParts);
    return block;
  }
  if (part.type === "thinking") {
    const details = node("details", null, "entry-part thinking");
    details.append(node("summary", "Thinking"), node("pre", part.thinking || "", "entry-text"));
    return details;
  }
  if (part.type === "toolCall") {
    const details = node("details", null, "entry-part tool-call");
    details.open = true;
    details.append(
      node("summary", `Tool call · ${part.name || "unknown"}`),
      node("pre", pretty(part.arguments ?? {}), "entry-json"),
    );
    return details;
  }
  if (part.type === "image") {
    return node("p", `Image · ${part.mimeType || "unknown type"} · ${part.sizeBytes || "unknown"} bytes`, "entry-part");
  }
  const details = node("details", null, "entry-part");
  details.append(node("summary", part.type || "Content"), node("pre", pretty(part), "entry-json"));
  return details;
}

function structuredPrompt(text) {
  const pattern = /<(untrusted_memory_context|untrusted_reference_context|current_request)>\s*([\s\S]*?)\s*<\/\1>/g;
  const parts = [];
  for (const match of text.matchAll(pattern)) {
    if (match[1] === "current_request") {
      const block = node("section", null, "entry-current-request");
      block.append(node("div", "Current request", "entry-section-label"));
      block.append(node("pre", match[2].trim(), "entry-text"));
      parts.push(block);
    } else {
      const details = node("details", null, "entry-part context");
      const label = match[1] === "untrusted_memory_context" ? "Memory context" : "Reference context";
      details.append(node("summary", label), node("pre", match[2].trim(), "entry-text"));
      parts.push(details);
    }
  }
  return parts;
}

function renderSessionEntry(entry, depth, isLeaf) {
  const message = entry.message && typeof entry.message === "object" ? entry.message : null;
  const role = String(message?.role || entry.type || "entry");
  const article = node("article", null, `session-entry${isLeaf ? " is-leaf" : ""}`);
  article.style.setProperty("--depth", String(depth));
  const header = node("div", null, "entry-header");
  const roleClass = role.toLowerCase().replace(/[^a-z0-9]/g, "");
  header.append(node("span", role, `entry-role ${roleClass}`));
  header.append(node("code", entry.id, "entry-id"));
  header.append(node("time", formatDate(entry.timestamp || message?.timestamp), "entry-time"));
  header.append(button("Continue", "entry-continue", () => openContinuation(entry.id)));
  article.append(header);
  const content = node("div", null, "entry-content");
  if (typeof message?.content === "string") {
    const promptParts = structuredPrompt(message.content);
    if (promptParts.length > 0) content.append(...promptParts);
    else content.append(node("pre", message.content, "entry-text"));
  } else if (Array.isArray(message?.content)) {
    for (const part of message.content) content.append(contentPart(part));
  } else if (message) {
    content.append(node("pre", pretty(message.content ?? message), "entry-text"));
  } else {
    content.append(node("pre", short(entry.summary || entry.name || entry.type, 1_000), "entry-text"));
  }
  if (message?.usage) content.append(node("div", `Usage · ${pretty(message.usage)}`, "entry-usage"));
  const raw = node("details", null, "raw-details");
  raw.append(node("summary", "Stored entry JSON"), node("pre", pretty(entry), "entry-json"));
  content.append(raw);
  article.append(content);
  return article;
}

function renderSessionDetail() {
  const detail = state.sessionDetail;
  elements.continueLeaf.hidden = !detail?.leafId;
  if (state.sessionDetailError) {
    elements.sessionTitle.textContent = "Session unavailable";
    elements.sessionMeta.textContent = state.sessionDetailError;
    elements.sessionTree.replaceChildren(node("p", state.sessionDetailError, "history-empty"));
    return;
  }
  if (!state.selectedSessionId) {
    elements.sessionTitle.textContent = "Select a session";
    elements.sessionMeta.textContent = "No session selected";
    elements.sessionTree.replaceChildren(node("p", "Select a session to inspect its stored entries.", "history-empty"));
    return;
  }
  if (!detail) {
    elements.sessionTitle.textContent = "Loading session";
    elements.sessionMeta.textContent = state.selectedSessionId;
    elements.sessionTree.replaceChildren(node("p", "Loading stored entries...", "history-empty"));
    return;
  }
  elements.sessionTitle.textContent = detail.name || short(currentRequest(detail.firstMessage), 90) || detail.id;
  elements.sessionMeta.textContent = `${detail.messageCount} messages · ${formatDate(detail.createdAt)} · ${detail.id}`;
  const byId = new Map(detail.entries.map((entry) => [entry.id, entry]));
  const cache = new Map();
  const entries = detail.entries.map((entry) =>
    renderSessionEntry(entry, entryDepth(entry, byId, cache), entry.id === detail.leafId),
  );
  const header = node("details", null, "session-entry");
  header.append(node("summary", "Session header JSON"), node("pre", pretty(detail.header), "entry-json"));
  elements.sessionTree.replaceChildren(header, ...entries);
}

function openContinuation(entryId) {
  const sessionId = state.selectedSessionId;
  if (!sessionId || !entryId) return;
  newChat();
  state.sessionId = sessionId;
  state.parentEntryId = entryId;
  renderResume();
  showView("playground");
  elements.prompt.focus();
}

function auditOptionLabel(audit) {
  return `${audit.status} · ${formatDate(audit.startedAt)} · ${short(audit.prompt, 70) || audit.runId}`;
}

function renderAudits() {
  elements.auditCount.textContent = `${state.auditTotal} runs`;
  elements.auditSelect.disabled = state.audits.length === 0;
  if (state.audits.length === 0) {
    const option = node("option", "No audited runs");
    option.value = "";
    elements.auditSelect.replaceChildren(option);
  } else {
    elements.auditSelect.replaceChildren(...state.audits.map((audit) => {
      const option = node("option", auditOptionLabel(audit));
      option.value = audit.runId;
      return option;
    }));
    elements.auditSelect.value = state.selectedAuditId || state.audits[0].runId;
  }
  const summary = state.audits.find((audit) => audit.runId === state.selectedAuditId);
  elements.auditSummary.textContent = summary
    ? `${summary.status} · ${summary.eventCount} events · ${summary.memoryScopeId || "memory off"} · ${summary.runId}`
    : "";
  if (state.auditError) {
    elements.auditEvents.replaceChildren(node("p", state.auditError, "history-empty"));
  } else if (!state.selectedSessionId) {
    elements.auditEvents.replaceChildren(node("p", "Detailed audit is unavailable until a session is selected.", "history-empty"));
  } else if (state.audits.length === 0) {
    elements.auditEvents.replaceChildren(node("p", "This session predates detailed run auditing, or was created outside the audited service.", "history-empty"));
  } else if (!state.auditDetail) {
    elements.auditEvents.replaceChildren(node("p", "Loading run events...", "history-empty"));
  } else {
    renderAuditEvents();
  }
}

async function selectAudit(runId) {
  state.selectedAuditId = runId;
  state.auditDetail = null;
  state.auditError = null;
  renderAudits();
  try {
    const detail = await requestJson(`/api/audits/${encodeURIComponent(runId)}`);
    if (state.selectedAuditId !== runId) return;
    state.auditDetail = detail;
  } catch (error) {
    if (state.selectedAuditId !== runId) return;
    state.auditError = error.message;
  }
  renderAudits();
}

function auditDescription(event) {
  const data = event.data || {};
  if (event.type === "memory.http.request") {
    const request = data.request || {};
    return `${data.operation || "memory"}${data.variant ? ` · ${data.variant}` : ""}\n${request.method || "GET"} ${request.url || ""}`;
  }
  if (event.type === "memory.http.response") {
    const response = data.response || {};
    return `${data.operation || "memory"} · HTTP ${response.status ?? "?"} · ${response.durationMs ?? "?"} ms · ${response.bodyBytes ?? "?"} bytes`;
  }
  if (event.type === "memory.http.error") return `${data.operation || "memory"} · ${data.error?.message || "request failed"}`;
  if (event.type === "tool.started") return `${data.toolName || "tool"} started\n${short(pretty(data.args), 300)}`;
  if (event.type === "tool.completed") return `${data.toolName || "tool"} ${data.isError ? "failed" : "completed"} · ${data.durationMs ?? "?"} ms`;
  if (event.type === "model.input") return `${data.model?.id || "model"} · ${(data.tools || []).length} tools\n${short(currentRequest(data.prompt), 300)}`;
  if (event.type === "model.turn.started") return `Model turn ${data.turn ?? "?"} started`;
  if (event.type === "model.turn.completed") return `Model turn ${data.turn ?? "?"} completed · ${data.durationMs ?? "?"} ms`;
  if (event.type === "memory.context") return `${(data.memories || []).length} memories merged from ${(data.queries || []).length} recall queries`;
  if (event.type === "run.request") return short(data.prompt, 300);
  if (event.type === "run.completed") return short(data.answer, 300);
  if (event.type === "run.failed") return `${data.code || "FAILED"} · ${data.message || data.error?.message || "Run failed"}`;
  if (event.type === "session.opened") return `${data.sessionId || "session"} from ${data.parentEntryId || "root"}`;
  return short(pretty(data), 300);
}

function auditCorrelation(data) {
  return [
    data.exchangeId && `exchange ${data.exchangeId}`,
    data.toolCallId && `tool ${data.toolCallId}`,
    data.sessionId && `session ${data.sessionId}`,
    data.entryId && `entry ${data.entryId}`,
  ].filter(Boolean).join(" · ");
}

function renderAuditEvents() {
  const events = state.auditDetail?.events || [];
  if (events.length === 0) {
    elements.auditEvents.replaceChildren(node("p", "No events recorded.", "history-empty"));
    return;
  }
  elements.auditEvents.replaceChildren(...events.map((event) => {
    const category = event.type.split(".")[0];
    const item = node("article", null, `audit-event ${category}`);
    const header = node("div", null, "audit-event-header");
    header.append(node("span", `#${event.sequence}`, "audit-sequence"));
    header.append(node("span", event.type, "audit-type"));
    header.append(node("time", formatDate(event.timestamp), "audit-time"));
    item.append(header, node("p", auditDescription(event), "audit-description"));
    const correlation = auditCorrelation(event.data || {});
    if (correlation) item.append(node("div", correlation, "audit-correlation"));
    const details = node("details");
    details.append(node("summary", "Event JSON"), node("pre", pretty(event), "audit-json"));
    item.append(details);
    return item;
  }));
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

document.querySelectorAll("[data-view]").forEach((button) => {
  button.addEventListener("click", () => showView(button.dataset.view));
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
elements.clearResume.addEventListener("click", newChat);
elements.refreshHistory.addEventListener("click", () => {
  state.sessions = [];
  state.sessionCursor = null;
  void loadSessions();
});
elements.sessionSearch.addEventListener("submit", (event) => {
  event.preventDefault();
  state.sessionQuery = elements.sessionQuery.value.trim();
  state.sessions = [];
  state.sessionCursor = null;
  void loadSessions();
});
elements.loadMoreSessions.addEventListener("click", () => void loadSessions({ append: true }));
elements.continueLeaf.addEventListener("click", () => {
  if (state.sessionDetail?.leafId) openContinuation(state.sessionDetail.leafId);
});
elements.auditSelect.addEventListener("change", () => {
  if (elements.auditSelect.value) void selectAudit(elements.auditSelect.value);
});
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
