import { randomUUID } from "node:crypto";

const MAX_QUERY_CHARS = 8_000;
const MAX_ANCHOR_CHARS = 3_000;
const MAX_CONTEXT_CHARS = 4_000;
const MAX_MEMORY_ITEMS = 50;
const MAX_RESPONSE_BYTES = 1024 * 1024;

function bounded(value, max) {
  const text = String(value ?? "").trim();
  return text.length <= max ? text : text.slice(0, max);
}

function oneLine(value, max) {
  return bounded(value, max).replace(/\s+/g, " ");
}

async function observeSafely(observe, type, data) {
  if (!observe) return;
  try {
    await observe({ type, data });
  } catch {
    // Observability must never make memory retrieval unavailable.
  }
}

function errorDetails(error) {
  return {
    name: bounded(error?.name || "Error", 128),
    message: bounded(error?.message || "Memory request failed", 1_000),
  };
}

export function buildMemoryQueries({ prompt, context, memory }) {
  const explicit = memory?.query?.trim();
  const sections = [explicit || `Current request: ${prompt.trim()}`];
  if (!explicit) {
    const references = context
      .filter((item) => item.kind === "reference")
      .map((item) => item.text.trim())
      .filter(Boolean);
    if (references.length > 0) {
      sections.push(`Reference context:\n${references.join("\n\n")}`);
    }
  }
  const unanchored = bounded(sections.join("\n"), MAX_QUERY_CHARS);
  const anchors = memory?.anchors ?? [];
  if (anchors.length === 0) return [unanchored];
  const labels = anchors.map(({ id, label }) =>
    label ? `${oneLine(label, 256)} (${oneLine(id, 256)})` : oneLine(id, 256),
  );
  const anchorSection = bounded(
    `Identity anchors for resolving references: ${labels.join(", ")}`,
    MAX_ANCHOR_CHARS,
  );
  const anchored = `${bounded(
    unanchored,
    MAX_QUERY_CHARS - anchorSection.length - 1,
  )}\n${anchorSection}`;
  return anchored === unanchored ? [unanchored] : [unanchored, anchored];
}

function optionalString(value, key) {
  const supplied = value[key];
  if (supplied === undefined || supplied === null) return null;
  if (typeof supplied !== "string") throw new Error("Malformed memory response");
  return supplied;
}

function parseMemories(payload) {
  if (!Array.isArray(payload?.results) || payload.results.length > 1_000) {
    throw new Error("Malformed memory response");
  }
  return payload.results.slice(0, MAX_MEMORY_ITEMS).map((item) => {
    if (
      !item ||
      typeof item !== "object" ||
      typeof item.id !== "string" ||
      item.id.length < 1 ||
      item.id.length > 128 ||
      typeof item.text !== "string" ||
      item.text.length < 1 ||
      item.text.length > 16_000 ||
      !Array.isArray(item.entities) ||
      !item.entities.every((entity) => typeof entity === "string")
    ) {
      throw new Error("Malformed memory response");
    }
    return {
      id: item.id,
      text: item.text,
      type: optionalString(item, "type"),
      entities: item.entities.slice(0, 100),
      occurredStart: optionalString(item, "occurred_start"),
      occurredEnd: optionalString(item, "occurred_end"),
      mentionedAt: optionalString(item, "mentioned_at"),
      documentId: optionalString(item, "document_id"),
      chunkId: optionalString(item, "chunk_id"),
    };
  });
}

async function recall({
  baseUrl,
  scopeId,
  query,
  timeoutMs,
  fetchImpl,
  observe,
  variant,
}) {
  const bank = encodeURIComponent(scopeId);
  const url = `${baseUrl.replace(/\/$/, "")}/v1/default/banks/${bank}/memories/recall`;
  const body = {
    query,
    budget: "mid",
    max_tokens: 2_000,
    types: ["world", "experience", "observation"],
    include: {
      entities: { max_tokens: 500 },
      source_facts: { max_tokens: 750 },
    },
  };
  const exchangeId = randomUUID();
  const startedAt = Date.now();
  await observeSafely(observe, "memory.http.request", {
    exchangeId,
    operation: "recall",
    variant,
    toolCallId: null,
    request: { method: "POST", url, body },
  });
  let response;
  let text;
  try {
    response = await fetchImpl(url, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(body),
      signal: AbortSignal.timeout(timeoutMs),
    });
    text = await response.text();
  } catch (error) {
    await observeSafely(observe, "memory.http.error", {
      exchangeId,
      operation: "recall",
      variant,
      toolCallId: null,
      durationMs: Math.max(0, Date.now() - startedAt),
      error: errorDetails(error),
    });
    throw new Error("Memory recall unavailable");
  }
  const bodyBytes = Buffer.byteLength(text);
  let payload;
  let malformed = false;
  if (bodyBytes <= MAX_RESPONSE_BYTES) {
    try {
      payload = JSON.parse(text);
    } catch {
      malformed = true;
    }
  }
  await observeSafely(observe, "memory.http.response", {
    exchangeId,
    operation: "recall",
    variant,
    toolCallId: null,
    response: {
      status: response.status,
      ok: response.ok,
      durationMs: Math.max(0, Date.now() - startedAt),
      bodyBytes,
      body:
        bodyBytes > MAX_RESPONSE_BYTES
          ? { omitted: true, reason: "response_too_large" }
          : malformed
            ? text
            : payload,
    },
  });
  if (!response.ok || Buffer.byteLength(text) > MAX_RESPONSE_BYTES) {
    throw new Error("Memory recall unavailable");
  }
  if (malformed) throw new Error("Malformed memory response");
  return parseMemories(payload);
}

function mergeByRank(groups) {
  const merged = [];
  const seen = new Set();
  const longest = Math.max(0, ...groups.map((group) => group.length));
  for (let rank = 0; rank < longest && merged.length < MAX_MEMORY_ITEMS; rank += 1) {
    for (const group of groups) {
      const memory = group[rank];
      if (!memory || seen.has(memory.id)) continue;
      seen.add(memory.id);
      merged.push(memory);
      if (merged.length === MAX_MEMORY_ITEMS) break;
    }
  }
  return merged;
}

function renderMemories(memories) {
  if (memories.length === 0) return { context: "", visible: [] };
  const lines = ["Relevant evidence recalled from the selected memory scope:"];
  const visible = [];
  for (const memory of memories) {
    const details = [];
    if (memory.type) details.push(memory.type);
    if (memory.occurredStart) {
      const occurred =
        memory.occurredEnd && memory.occurredEnd !== memory.occurredStart
          ? `${memory.occurredStart} to ${memory.occurredEnd}`
          : memory.occurredStart;
      details.push(`occurred ${occurred}`);
    }
    if (memory.mentionedAt) details.push(`mentioned ${memory.mentionedAt}`);
    if (memory.entities.length > 0) {
      details.push(`entities: ${memory.entities.join(", ")}`);
    }
    if (memory.documentId) {
      details.push(
        `source: ${memory.documentId}${memory.chunkId ? `#${memory.chunkId}` : ""}`,
      );
    }
    const detailText = bounded(details.join("; "), 1_500);
    const suffix = ` (${detailText ? `${detailText}; ` : ""}memory_id: ${memory.id})`;
    const used = lines.join("\n").length + 1;
    const textBudget = MAX_CONTEXT_CHARS - used - 2 - suffix.length;
    if (textBudget < 4) break;
    const memoryText =
      memory.text.length <= textBudget
        ? memory.text
        : `${memory.text.slice(0, textBudget - 3)}...`;
    const line = `- ${memoryText}${suffix}`;
    lines.push(line);
    visible.push(memory);
  }
  return { context: lines.join("\n"), visible };
}

export async function retrieveMemoryContext({
  baseUrl,
  prompt,
  context,
  memory,
  timeoutMs = 30_000,
  fetchImpl = fetch,
  observe = null,
}) {
  if (!baseUrl || !memory) {
    return { queries: [], memories: [], context: "", access: null };
  }
  const queries = buildMemoryQueries({ prompt, context, memory });
  const settled = await Promise.allSettled(
    queries.map((query, index) =>
      recall({
        baseUrl,
        scopeId: memory.scopeId,
        query,
        timeoutMs,
        fetchImpl,
        observe,
        variant: index === 0 ? "unanchored" : "anchored",
      }),
    ),
  );
  const groups = settled
    .filter((item) => item.status === "fulfilled")
    .map((item) => item.value);
  if (groups.length === 0) {
    return { queries, memories: [], context: "", access: null };
  }
  const memories = mergeByRank(groups);
  const rendered = renderMemories(memories);
  return {
    queries,
    memories: rendered.visible,
    context: rendered.context,
    access: {
      bankId: memory.scopeId,
      references: rendered.visible.map((item) => ({
        memoryId: item.id,
        documentId: item.documentId,
        chunkId: item.chunkId,
      })),
    },
  };
}
