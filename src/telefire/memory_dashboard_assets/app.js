const state = { banks: [], activeBank: null, activeData: null, tab: "overview" };

const elements = {
  status: document.querySelector("#service-status"),
  refresh: document.querySelector("#refresh"),
  count: document.querySelector("#bank-count"),
  search: document.querySelector("#bank-search"),
  list: document.querySelector("#bank-list"),
  empty: document.querySelector("#empty-state"),
  view: document.querySelector("#bank-view"),
  title: document.querySelector("#bank-title"),
  key: document.querySelector("#bank-key"),
  health: document.querySelector("#bank-health"),
  dialog: document.querySelector("#source-dialog"),
  evidenceTitle: document.querySelector("#evidence-title"),
  sourceId: document.querySelector("#source-id"),
  sourceContent: document.querySelector("#source-content"),
};

function node(tag, text, className) {
  const item = document.createElement(tag);
  if (text !== undefined && text !== null) item.textContent = String(text);
  if (className) item.className = className;
  return item;
}

function time(value) {
  if (!value) return "Never";
  const date = typeof value === "number" ? new Date(value * 1000) : new Date(value);
  return Number.isNaN(date.valueOf()) ? "Unknown" : date.toLocaleString();
}

function listItems(payload, key = "items") {
  return Array.isArray(payload?.[key]) ? payload[key] : [];
}

function known(value) {
  if (value === null || value === undefined) return "Unknown";
  return value;
}

function booleanState(value, whenTrue, whenFalse) {
  if (value === true) return whenTrue;
  if (value === false) return whenFalse;
  return "Unknown";
}

async function request(path) {
  const response = await fetch(path, { headers: { accept: "application/json" } });
  if (!response.ok) throw new Error(`Request failed: ${response.status}`);
  return response.json();
}

async function loadBanks() {
  elements.status.textContent = "Loading banks";
  try {
    const payload = await request("/api/banks");
    state.banks = payload.items || [];
    elements.status.textContent = "Connected";
    elements.count.textContent = String(payload.total || 0);
    renderBankList();
    if (state.activeBank && state.banks.some((item) => item.bank_id === state.activeBank)) {
      await selectBank(state.activeBank);
    }
  } catch {
    elements.status.textContent = "Inspection unavailable";
    state.banks = [];
    renderBankList();
  }
}

function renderBankList() {
  const query = elements.search.value.trim().toLocaleLowerCase();
  elements.list.replaceChildren();
  const filtered = state.banks.filter((bank) => {
    const value = `${bank.display_name || ""} ${bank.bank_id}`.toLocaleLowerCase();
    return !query || value.includes(query);
  });
  for (const bank of filtered) {
    const button = node("button", null, "bank-item");
    button.type = "button";
    button.setAttribute("role", "option");
    button.setAttribute("aria-selected", String(bank.bank_id === state.activeBank));
    button.append(node("span", bank.display_name || bank.bank_id, "bank-name"));
    const meta = node("span", null, "bank-meta");
    meta.append(node("span", `${bank.fact_count || 0} memories`));
    meta.append(node("span", booleanState(bank.enabled, "Enabled", "Manual")));
    button.append(meta);
    button.addEventListener("click", () => void selectBank(bank.bank_id));
    elements.list.append(button);
  }
}

async function selectBank(bankId) {
  state.activeBank = bankId;
  renderBankList();
  elements.empty.hidden = true;
  elements.view.hidden = false;
  elements.title.textContent = state.banks.find((item) => item.bank_id === bankId)?.display_name || bankId;
  elements.key.textContent = bankId;
  for (const id of ["overview", "episodes", "memories", "entities"]) {
    document.querySelector(`#tab-${id}`).replaceChildren(node("p", "Loading"));
  }
  try {
    state.activeData = await request(`/api/banks/${encodeURIComponent(bankId)}`);
    renderBank();
  } catch {
    elements.health.replaceChildren(status("Bank unavailable", "error"));
  }
}

function status(text, kind = "") {
  return node("span", text, `status ${kind}`.trim());
}

function renderBank() {
  const data = state.activeData;
  elements.health.replaceChildren(
    status(
      `Automatic capture: ${booleanState(data.enabled, "Enabled", "Disabled")}`,
      data.enabled === true ? "enabled" : "",
    ),
    status(`Receipts: ${known(data.receipt_count)}`),
    ...(data.dream?.last_error ? [status("Dream error", "error")] : []),
  );
  renderOverview(data);
  renderEpisodes(data);
  renderMemories(data);
  renderEntities(data);
  showTab(state.tab);
}

function metric(label, value) {
  const item = node("div", null, "metric");
  item.append(node("div", label, "metric-label"), node("div", value, "metric-value"));
  return item;
}

function renderOverview(data) {
  const panel = document.querySelector("#tab-overview");
  const memories = listItems(data.memories);
  const documents = listItems(data.documents);
  const entities = listItems(data.entities);
  const observationScopes = listItems(data.observations, "scopes");
  const dreamKnown = data.dream !== null && data.dream !== undefined;
  const metrics = node("div", null, "metric-grid");
  metrics.append(
    metric("Memories", data.memories?.total ?? memories.length),
    metric("Episodes", data.documents?.total ?? documents.length),
    metric("Entities", data.entities?.total ?? entities.length),
    metric("Observations", observationScopes.reduce((total, item) => total + (item.count || 0), 0)),
  );
  const operations = table(
    ["Capture", "Cursor", "Scanned through", "Last attempt", "Last success", "Failure"],
    [[
      booleanState(data.enabled, "Enabled", "Disabled"),
      dreamKnown ? data.dream.cursor_message_id ?? "None" : "Unknown",
      dreamKnown ? time(data.dream.scanned_until_at) : "Unknown",
      dreamKnown ? time(data.dream.last_attempt_at) : "Unknown",
      dreamKnown ? time(data.dream.last_success_at) : "Unknown",
      dreamKnown ? data.dream.last_error || "None" : "Unknown",
    ]],
  );
  panel.replaceChildren(metrics, node("h3", "Delivery State", "section-title"), operations);
}

function table(headings, rows, widths = []) {
  const item = node("table", null, "data-table");
  const head = document.createElement("thead");
  const headRow = document.createElement("tr");
  headings.forEach((heading, index) => {
    const cell = node("th", heading);
    if (widths[index]) cell.style.width = widths[index];
    headRow.append(cell);
  });
  head.append(headRow);
  const body = document.createElement("tbody");
  if (rows.length === 0) {
    const row = document.createElement("tr");
    const cell = node("td", "No records", "empty-row");
    cell.colSpan = headings.length;
    row.append(cell);
    body.append(row);
  } else {
    for (const values of rows) {
      const row = document.createElement("tr");
      for (const value of values) {
        const cell = document.createElement("td");
        cell.append(value instanceof Node ? value : document.createTextNode(String(value ?? "")));
        row.append(cell);
      }
      body.append(row);
    }
  }
  item.append(head, body);
  return item;
}

function renderEpisodes(data) {
  const rows = listItems(data.documents).map((document) => {
    const open = node("button", document.id, "source-button");
    open.type = "button";
    open.addEventListener("click", () => void openSource(document.id));
    return [open, document.memory_unit_count || 0, time(document.updated_at), document.content_hash || ""];
  });
  document.querySelector("#tab-episodes").replaceChildren(
    table(["Document", "Memories", "Updated", "Content version"], rows, ["38%", "10%", "20%", "32%"]),
  );
}

function renderMemories(data) {
  const labels = data.actor_labels || {};
  const rows = listItems(data.memories).map((memory) => {
    const text = node("div", memory.text || "", "memory-text");
    const entities = Array.isArray(memory.entities)
      ? memory.entities
      : typeof memory.entities === "string"
        ? memory.entities.split(",").map((value) => value.trim()).filter(Boolean)
        : [];
    const entityText = entities.map((id) => labels[id] ? `${labels[id]} (${id})` : id).join(", ");
    const source = memory.document_id ? node("button", memory.document_id, "source-button") : "";
    if (source instanceof Node) {
      source.type = "button";
      source.addEventListener("click", () => void openSource(memory.document_id));
    }
    const inspect = node("button", "Inspect", "source-button");
    inspect.type = "button";
    inspect.addEventListener("click", () => void openMemory(memory.id));
    return [memory.fact_type || memory.type || "", text, entityText, time(memory.occurred_start || memory.mentioned_at), memory.state || "valid", source, inspect];
  });
  const memoryTable = table(
    ["Type", "Memory", "Entities", "Time", "State", "Source", "Evidence"],
    rows,
    ["90px", "320px", "220px", "160px", "90px", "180px", "90px"],
  );
  memoryTable.style.minWidth = "1150px";
  document.querySelector("#tab-memories").replaceChildren(memoryTable);
}

function renderEntities(data) {
  const labels = data.actor_labels || {};
  const rows = listItems(data.entities).map((entity) => [
    labels[entity.canonical_name] || "",
    node("code", entity.canonical_name || entity.id || ""),
    entity.mention_count || 0,
    time(entity.first_seen),
    time(entity.last_seen),
  ]);
  document.querySelector("#tab-entities").replaceChildren(
    table(["Display name", "Canonical key", "Mentions", "First seen", "Last seen"], rows, ["20%", "32%", "10%", "19%", "19%"]),
  );
}

function showTab(tab) {
  state.tab = tab;
  document.querySelectorAll("[role=tab]").forEach((button) => {
    button.setAttribute("aria-selected", String(button.dataset.tab === tab));
  });
  for (const id of ["overview", "episodes", "memories", "entities"]) {
    document.querySelector(`#tab-${id}`).hidden = id !== tab;
  }
}

async function openSource(documentId) {
  elements.evidenceTitle.textContent = "Source Evidence";
  elements.sourceId.textContent = documentId;
  elements.sourceContent.replaceChildren(node("p", "Loading"));
  elements.dialog.showModal();
  try {
    const payload = await request(
      `/api/banks/${encodeURIComponent(state.activeBank)}/documents/${encodeURIComponent(documentId)}`,
    );
    const blocks = [node("pre", payload.document?.original_text || "No source text", "source-block")];
    for (const chunk of listItems(payload.chunks)) {
      blocks.push(node("h3", `Chunk ${chunk.chunk_index ?? ""}`, "section-title"));
      blocks.push(node("pre", chunk.chunk_text || "", "source-block"));
    }
    elements.sourceContent.replaceChildren(...blocks);
  } catch {
    elements.sourceContent.replaceChildren(node("p", "Source unavailable"));
  }
}

async function openMemory(memoryId) {
  elements.evidenceTitle.textContent = "Memory Evidence";
  elements.sourceId.textContent = memoryId;
  elements.sourceContent.replaceChildren(node("p", "Loading"));
  elements.dialog.showModal();
  try {
    const payload = await request(
      `/api/banks/${encodeURIComponent(state.activeBank)}/memories/${encodeURIComponent(memoryId)}`,
    );
    const memory = payload.memory || {};
    const entities = Array.isArray(memory.entities) ? memory.entities.join(", ") : memory.entities || "";
    const metadata = table(
      ["Field", "Value"],
      [
        ["Type", memory.type || memory.fact_type || "Unknown"],
        ["State", memory.state || "Unknown"],
        ["Occurred", time(memory.occurred_start || memory.date)],
        ["Mentioned", time(memory.mentioned_at)],
        ["Entities", entities || "None"],
        ["Source document", memory.document_id || "Unavailable"],
        ["Invalidation", memory.invalidation_reason || "None"],
      ],
    );
    const blocks = [node("p", memory.text || "No memory text", "memory-text"), metadata];
    const sources = Array.isArray(memory.source_memories) ? memory.source_memories : [];
    if (sources.length > 0) {
      blocks.push(node("h3", "Supporting Statements", "section-title"));
      blocks.push(table(
        ["Type", "Statement", "Occurred"],
        sources.map((source) => [
          source.type || "",
          source.text || "",
          time(source.occurred_start || source.mentioned_at),
        ]),
      ));
    }
    const history = Array.isArray(payload.history) ? payload.history : [];
    blocks.push(node("h3", "Revision History", "section-title"));
    blocks.push(table(
      ["State", "Reason", "Changed"],
      history.map((entry) => [
        entry.state || entry.action || "Unknown",
        entry.reason || entry.invalidation_reason || "",
        time(entry.changed_at || entry.created_at || entry.invalidated_at),
      ]),
    ));
    elements.sourceContent.replaceChildren(...blocks);
  } catch {
    elements.sourceContent.replaceChildren(node("p", "Memory evidence unavailable"));
  }
}

elements.refresh.addEventListener("click", () => void loadBanks());
elements.search.addEventListener("input", renderBankList);
elements.closeSource = document.querySelector("#close-source");
elements.closeSource.addEventListener("click", () => elements.dialog.close());
document.querySelectorAll("[role=tab]").forEach((button) => {
  button.addEventListener("click", () => showTab(button.dataset.tab));
});

void loadBanks();
