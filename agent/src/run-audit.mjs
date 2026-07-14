import { createHash } from "node:crypto";
import {
  appendFile,
  mkdir,
  open,
  readdir,
} from "node:fs/promises";
import { join } from "node:path";

const RUN_ID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const MAX_LIST_LIMIT = 100;
const MAX_EVENT_BYTES = 2 * 1024 * 1024;
const MAX_FILE_BYTES = 64 * 1024 * 1024;
const MAX_STRING_CHARS = 1024 * 1024;
const MAX_ARRAY_ITEMS = 5_000;
const MAX_OBJECT_KEYS = 1_000;
const MAX_DEPTH = 16;

function isPrivateKey(key) {
  const normalized = key.replace(/[^a-z0-9]/gi, "").toLowerCase();
  return (
    normalized === "authorization" ||
    normalized === "cookie" ||
    normalized === "setcookie" ||
    normalized === "password" ||
    normalized === "errormessage" ||
    normalized === "thinkingsignature" ||
    normalized === "apikey" ||
    normalized.endsWith("token") ||
    normalized.includes("secret")
  );
}

function sanitizedUrl(value) {
  if (typeof value !== "string") return sanitizeAuditValue(value);
  try {
    const parsed = new URL(value);
    parsed.username = "";
    parsed.password = "";
    for (const key of parsed.searchParams.keys()) {
      const normalized = key.replace(/[^a-z0-9]/gi, "").toLowerCase();
      if (
        isPrivateKey(key) ||
        normalized === "key" ||
        normalized === "sig" ||
        normalized.includes("signature")
      ) {
        parsed.searchParams.set(key, "[REDACTED]");
      }
    }
    return bounded(parsed.toString());
  } catch {
    return bounded(value);
  }
}

function bounded(value, max = MAX_STRING_CHARS) {
  const text = String(value ?? "");
  return text.length <= max ? text : `${text.slice(0, max - 3)}...`;
}

function imageMetadata(value) {
  const supplied = typeof value.data === "string" ? value.data : "";
  const padding = supplied.endsWith("==") ? 2 : supplied.endsWith("=") ? 1 : 0;
  return {
    type: "image",
    mimeType: bounded(value.mimeType, 256),
    sizeBytes: Math.max(0, Math.floor((supplied.length * 3) / 4) - padding),
    data: "[OMITTED]",
  };
}

export function sanitizeAuditValue(value, depth = 0, seen = new WeakSet()) {
  if (value === null || value === undefined) return value ?? null;
  if (typeof value === "string") return bounded(value);
  if (typeof value === "number" || typeof value === "boolean") return value;
  if (typeof value === "bigint") return value.toString();
  if (typeof value !== "object") return bounded(value, 1_000);
  if (depth >= MAX_DEPTH) return "[DEPTH_LIMIT]";
  if (seen.has(value)) return "[CIRCULAR]";
  seen.add(value);
  try {
    if (value.type === "image" && "data" in value) return imageMetadata(value);
    if (Array.isArray(value)) {
      return value
        .slice(0, MAX_ARRAY_ITEMS)
        .map((item) => sanitizeAuditValue(item, depth + 1, seen));
    }
    const result = {};
    for (const [key, item] of Object.entries(value).slice(0, MAX_OBJECT_KEYS)) {
      const normalized = key.replace(/[^a-z0-9]/gi, "").toLowerCase();
      if (isPrivateKey(key)) result[key] = "[REDACTED]";
      else if (normalized === "url" || normalized.endsWith("url")) {
        result[key] = sanitizedUrl(item);
      } else if (
        (normalized === "urls" || normalized.endsWith("urls")) &&
        Array.isArray(item)
      ) {
        result[key] = item
          .slice(0, MAX_ARRAY_ITEMS)
          .map((url) => sanitizedUrl(url));
      } else result[key] = sanitizeAuditValue(item, depth + 1, seen);
    }
    return result;
  } finally {
    seen.delete(value);
  }
}

function encodeEvent(event) {
  const line = JSON.stringify(event);
  if (Buffer.byteLength(line) <= MAX_EVENT_BYTES) return `${line}\n`;
  const digest = createHash("sha256").update(line).digest("hex");
  return `${JSON.stringify({
    ...event,
    data: {
      truncated: true,
      originalBytes: Buffer.byteLength(line),
      sha256: digest,
    },
  })}\n`;
}

class RunAuditRecorder {
  constructor(path, runId) {
    this.path = path;
    this.runId = runId;
    this.sequence = 0;
    this.tail = Promise.resolve();
  }

  record(type, data = {}) {
    const event = {
      version: 1,
      sequence: (this.sequence += 1),
      timestamp: new Date().toISOString(),
      runId: this.runId,
      type: bounded(type, 128),
      data: sanitizeAuditValue(data),
    };
    const line = encodeEvent(event);
    this.tail = this.tail.then(() =>
      appendFile(this.path, line, { encoding: "utf8", mode: 0o600 }),
    );
    return this.tail;
  }

  flush() {
    return this.tail;
  }
}

function parseAudit(content, expectedRunId) {
  const events = [];
  const lines = content.split("\n");
  for (const [index, line] of lines.entries()) {
    if (!line.trim()) continue;
    let event;
    try {
      event = JSON.parse(line);
    } catch (error) {
      if (index === lines.length - 1 && !content.endsWith("\n")) break;
      throw error;
    }
    if (
      !event ||
      event.version !== 1 ||
      event.runId !== expectedRunId ||
      !Number.isInteger(event.sequence) ||
      typeof event.timestamp !== "string" ||
      typeof event.type !== "string"
    ) {
      throw new Error("Malformed run audit");
    }
    events.push(event);
  }
  events.sort((left, right) => left.sequence - right.sequence);
  return events;
}

function eventData(events, type) {
  return [...events].reverse().find((event) => event.type === type)?.data ?? null;
}

function summarize(runId, events) {
  const request = events.find((event) => event.type === "run.request")?.data ?? {};
  const opened = eventData(events, "session.opened") ?? {};
  const completed = eventData(events, "run.completed");
  const failed = eventData(events, "run.failed");
  const terminal = [...events]
    .reverse()
    .find((event) => event.type === "run.completed" || event.type === "run.failed");
  return {
    runId,
    sessionId:
      completed?.sessionId ?? opened.sessionId ?? request.sessionId ?? null,
    entryId: completed?.entryId ?? null,
    status: completed ? "completed" : failed ? "failed" : "in_progress",
    startedAt: events[0]?.timestamp ?? null,
    finishedAt: terminal?.timestamp ?? null,
    prompt: bounded(request.prompt, 300),
    memoryScopeId: request.memory?.scopeId ?? null,
    eventCount: events.length,
  };
}

export class RunAuditStore {
  constructor(directory) {
    this.directory = directory;
  }

  #path(runId) {
    return join(this.directory, `${runId}.jsonl`);
  }

  async start(runId) {
    if (!RUN_ID_RE.test(runId ?? "")) throw new Error("Invalid run id");
    await mkdir(this.directory, { recursive: true, mode: 0o700 });
    const handle = await open(this.#path(runId), "wx", 0o600);
    await handle.close();
    return new RunAuditRecorder(this.#path(runId), runId);
  }

  async get(runId) {
    if (!RUN_ID_RE.test(runId ?? "")) return null;
    const path = this.#path(runId);
    let handle;
    try {
      handle = await open(path, "r");
      const info = await handle.stat();
      if (!info.isFile() || info.size > MAX_FILE_BYTES) return null;
      const events = parseAudit(await handle.readFile("utf8"), runId);
      return { runId, events };
    } catch {
      return null;
    } finally {
      await handle?.close();
    }
  }

  async list({ limit = 50, cursor = null, sessionId = null } = {}) {
    const pageSize = Math.max(1, Math.min(MAX_LIST_LIMIT, Number(limit) || 50));
    let names;
    try {
      names = await readdir(this.directory);
    } catch {
      names = [];
    }
    const summaries = [];
    for (const name of names.slice(0, 20_000)) {
      if (!name.endsWith(".jsonl")) continue;
      const runId = name.slice(0, -6);
      if (!RUN_ID_RE.test(runId)) continue;
      const audit = await this.get(runId);
      if (!audit || audit.events.length === 0) continue;
      const item = summarize(runId, audit.events);
      if (sessionId && item.sessionId !== sessionId) continue;
      summaries.push(item);
    }
    summaries.sort((left, right) => {
      const started = String(right.startedAt).localeCompare(String(left.startedAt));
      return started || left.runId.localeCompare(right.runId);
    });
    const cursorIndex = cursor
      ? summaries.findIndex((item) => item.runId === cursor)
      : -1;
    const start = cursor ? (cursorIndex < 0 ? summaries.length : cursorIndex + 1) : 0;
    const selected = summaries.slice(start, start + pageSize);
    return {
      items: selected,
      total: summaries.length,
      nextCursor:
        start + selected.length < summaries.length
          ? selected.at(-1)?.runId ?? null
          : null,
    };
  }
}
