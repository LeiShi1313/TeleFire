"use strict";

const elements = {
  connectionStatus: document.querySelector("#connection-status"),
  refreshButton: document.querySelector("#refresh-button"),
  retryButton: document.querySelector("#retry-button"),
  pageError: document.querySelector("#page-error"),
  pageErrorMessage: document.querySelector("#page-error-message"),
  subjectSearch: document.querySelector("#subject-search"),
  subjectCount: document.querySelector("#subject-count"),
  subjectLoading: document.querySelector("#subject-loading"),
  subjectList: document.querySelector("#subject-list"),
  subjectEmpty: document.querySelector("#subject-empty"),
  noSelection: document.querySelector("#no-selection"),
  subjectView: document.querySelector("#subject-view"),
  subjectDisplayName: document.querySelector("#subject-display-name"),
  subjectId: document.querySelector("#subject-id"),
  subjectUpdated: document.querySelector("#subject-updated"),
  subjectStats: document.querySelector("#subject-stats"),
  profileContent: document.querySelector("#profile-content"),
  recordFilters: document.querySelector("#record-filters"),
  recordQuery: document.querySelector("#record-query"),
  scopeFilter: document.querySelector("#scope-filter"),
  typeFilter: document.querySelector("#type-filter"),
  statusFilter: document.querySelector("#status-filter"),
  recordCount: document.querySelector("#record-count"),
  recordsLoading: document.querySelector("#records-loading"),
  recordList: document.querySelector("#record-list"),
  recordsEmpty: document.querySelector("#records-empty"),
  loadMore: document.querySelector("#load-more"),
};

const state = {
  subjects: [],
  selectedSubjectId: null,
  recordOffset: 0,
  recordTotal: 0,
  requestVersion: 0,
  scopeDisplayNames: {},
};

const dateFormatter = new Intl.DateTimeFormat(undefined, {
  dateStyle: "medium",
  timeStyle: "short",
});

function createElement(tagName, className, text) {
  const node = document.createElement(tagName);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function formatDate(value) {
  if (!value) return "No activity";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : dateFormatter.format(date);
}

function pluralize(count, noun) {
  return `${count} ${noun}${count === 1 ? "" : "s"}`;
}

function identityLabel(key, displayName) {
  return displayName ? `${displayName} (${key})` : key;
}

function setConnection(text, className) {
  elements.connectionStatus.textContent = text;
  elements.connectionStatus.className = "connection-status";
  if (className) elements.connectionStatus.classList.add(className);
}

function showError(message) {
  elements.pageErrorMessage.textContent = message;
  elements.pageError.hidden = false;
  setConnection("Unavailable", "failed");
}

function clearError() {
  elements.pageError.hidden = true;
}

async function requestJson(url) {
  const response = await fetch(url, {
    headers: { Accept: "application/json" },
    cache: "no-store",
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(payload.error || `Request failed with status ${response.status}`);
  }
  return payload;
}

function renderSubjects() {
  const search = elements.subjectSearch.value.trim().toLocaleLowerCase();
  const visible = state.subjects.filter((subject) => {
    const searchable = [subject.subject_id, subject.subject_display_name || ""]
      .join(" ")
      .toLocaleLowerCase();
    return searchable.includes(search);
  });
  const rows = visible.map((subject) => {
    const button = createElement("button", "subject-item");
    button.type = "button";
    button.dataset.subjectId = subject.subject_id;
    button.setAttribute(
      "aria-current",
      String(subject.subject_id === state.selectedSubjectId),
    );

    const displayName = subject.subject_display_name;
    button.append(
      createElement(
        "span",
        displayName ? "subject-item-name" : "subject-item-name canonical",
        displayName || subject.subject_id,
      ),
    );
    if (displayName) {
      button.append(createElement("span", "subject-item-id", subject.subject_id));
    }
    const meta = createElement("span", "subject-item-meta");
    const total = Object.values(subject.counts).reduce((sum, count) => sum + count, 0);
    meta.append(
      createElement("span", "", pluralize(total, "record")),
      createElement("span", "", formatDate(subject.last_occurred_at)),
    );
    button.append(meta);
    button.addEventListener("click", () => selectSubject(subject.subject_id));
    return button;
  });
  elements.subjectList.replaceChildren(...rows);
  elements.subjectEmpty.hidden = visible.length !== 0;
}

function renderSubjectHeader(detail) {
  elements.subjectDisplayName.textContent =
    detail.subject_display_name || detail.subject_id;
  elements.subjectId.textContent = detail.subject_id;
  elements.subjectId.hidden = !detail.subject_display_name;
  elements.subjectUpdated.textContent = `Last activity ${formatDate(detail.last_occurred_at)}`;
  const stats = Object.entries(detail.counts).map(([kind, count]) =>
    createElement("span", "stat-pill", `${kind} ${count}`),
  );
  if (detail.suppressed_count) {
    stats.push(
      createElement("span", "status-badge", `${detail.suppressed_count} suppressed`),
    );
  }
  elements.subjectStats.replaceChildren(...stats);
  elements.profileContent.textContent = detail.profile || "No profile stored.";

  state.scopeDisplayNames = detail.scope_display_names || {};
  const options = [createElement("option", "", "All scopes")];
  options[0].value = "";
  for (const scope of detail.scopes) {
    const option = createElement(
      "option",
      "",
      identityLabel(scope, state.scopeDisplayNames[scope]),
    );
    option.value = scope;
    options.push(option);
  }
  elements.scopeFilter.replaceChildren(...options);
}

function renderRecord(record) {
  const row = createElement("article", "record-row");
  const header = createElement("div", "record-header");
  const type = createElement("span", `type-badge ${record.record_type}`, record.record_type);
  header.append(type);
  if (record.suppressed) {
    header.append(createElement("span", "status-badge", "suppressed"));
  }
  if (record.scope_id) {
    header.append(
      createElement(
        "span",
        "record-scope",
        identityLabel(
          record.scope_id,
          record.scope_display_name || state.scopeDisplayNames[record.scope_id],
        ),
      ),
    );
  }
  header.append(createElement("time", "record-time", formatDate(record.occurred_at)));
  row.append(header, createElement("p", "record-text", record.text));

  if (record.metadata && Object.keys(record.metadata).length) {
    const details = createElement("details", "record-metadata");
    details.append(createElement("summary", "", "Metadata"));
    details.append(
      createElement("pre", "", JSON.stringify(record.metadata, null, 2)),
    );
    row.append(details);
  }
  return row;
}

function recordsUrl(subjectId, offset) {
  const path = `/v1/memory/subjects/${encodeURIComponent(subjectId)}/records`;
  const params = new URLSearchParams({
    limit: "100",
    offset: String(offset),
    status: elements.statusFilter.value,
  });
  if (elements.scopeFilter.value) params.set("scope_id", elements.scopeFilter.value);
  if (elements.typeFilter.value) params.set("record_type", elements.typeFilter.value);
  if (elements.recordQuery.value.trim()) {
    params.set("query", elements.recordQuery.value.trim());
  }
  return `${path}?${params.toString()}`;
}

async function loadRecords({ append = false, version = state.requestVersion } = {}) {
  const subjectId = state.selectedSubjectId;
  if (!subjectId) return;
  const offset = append ? state.recordOffset : 0;
  elements.recordsLoading.hidden = append;
  elements.recordsEmpty.hidden = true;
  elements.loadMore.hidden = true;
  if (!append) elements.recordList.replaceChildren();

  try {
    const page = await requestJson(recordsUrl(subjectId, offset));
    if (version !== state.requestVersion || subjectId !== state.selectedSubjectId) return;
    const rows = page.items.map(renderRecord);
    if (append) elements.recordList.append(...rows);
    else elements.recordList.replaceChildren(...rows);
    state.recordOffset = page.offset + page.items.length;
    state.recordTotal = page.total;
    elements.recordCount.textContent = page.is_truncated
      ? `${pluralize(page.total, "record")} (partial)`
      : pluralize(page.total, "record");
    elements.recordsEmpty.hidden = page.total !== 0;
    elements.loadMore.hidden = state.recordOffset >= page.total;
    clearError();
    setConnection("Connected", "connected");
  } catch (error) {
    if (version === state.requestVersion) showError(error.message);
  } finally {
    if (version === state.requestVersion) elements.recordsLoading.hidden = true;
  }
}

async function selectSubject(subjectId) {
  state.selectedSubjectId = subjectId;
  state.recordOffset = 0;
  state.requestVersion += 1;
  const version = state.requestVersion;
  renderSubjects();
  elements.noSelection.hidden = true;
  elements.subjectView.hidden = false;
  elements.subjectDisplayName.textContent = subjectId;
  elements.subjectId.textContent = subjectId;
  elements.subjectId.hidden = true;
  elements.subjectUpdated.textContent = "Loading";
  elements.subjectStats.replaceChildren();
  elements.profileContent.textContent = "Loading profile...";
  elements.scopeFilter.value = "";

  try {
    const detailPath = `/v1/memory/subjects/${encodeURIComponent(subjectId)}`;
    const detail = await requestJson(detailPath);
    if (version !== state.requestVersion) return;
    renderSubjectHeader(detail);
    clearError();
    setConnection("Connected", "connected");
    await loadRecords({ version });
  } catch (error) {
    if (version === state.requestVersion) showError(error.message);
  }
}

async function refreshSubjects() {
  const previous = state.selectedSubjectId;
  elements.refreshButton.disabled = true;
  elements.subjectLoading.hidden = false;
  elements.subjectList.hidden = true;
  elements.subjectEmpty.hidden = true;
  clearError();
  setConnection("Connecting");
  try {
    const page = await requestJson("/v1/memory/subjects?limit=500&offset=0");
    state.subjects = page.items;
    elements.subjectCount.textContent = page.is_truncated
      ? `${page.total}+`
      : String(page.total);
    renderSubjects();
    elements.subjectList.hidden = false;
    setConnection("Connected", "connected");
    const nextSubject = state.subjects.some((item) => item.subject_id === previous)
      ? previous
      : state.subjects[0]?.subject_id;
    if (nextSubject) await selectSubject(nextSubject);
    else {
      state.selectedSubjectId = null;
      elements.noSelection.hidden = false;
      elements.subjectView.hidden = true;
    }
  } catch (error) {
    showError(error.message);
  } finally {
    elements.subjectLoading.hidden = true;
    elements.refreshButton.disabled = false;
  }
}

let queryTimer;
elements.subjectSearch.addEventListener("input", renderSubjects);
elements.refreshButton.addEventListener("click", refreshSubjects);
elements.retryButton.addEventListener("click", refreshSubjects);
elements.loadMore.addEventListener("click", () => loadRecords({ append: true }));
elements.recordFilters.addEventListener("submit", (event) => event.preventDefault());
elements.scopeFilter.addEventListener("change", () => loadRecords());
elements.typeFilter.addEventListener("change", () => loadRecords());
elements.statusFilter.addEventListener("change", () => loadRecords());
elements.recordQuery.addEventListener("input", () => {
  window.clearTimeout(queryTimer);
  queryTimer = window.setTimeout(() => loadRecords(), 250);
});

refreshSubjects();
