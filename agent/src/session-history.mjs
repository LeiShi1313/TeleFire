import { SessionManager } from "@earendil-works/pi-coding-agent";

const IDENTIFIER_RE = /^[A-Za-z0-9_-]{1,128}$/;
const MAX_LIST_LIMIT = 100;
const MAX_STRING_CHARS = 64_000;
const MAX_ARRAY_ITEMS = 2_000;
const MAX_OBJECT_KEYS = 500;
const MAX_DEPTH = 12;

function bounded(value, max) {
  const text = String(value ?? "");
  return text.length <= max ? text : `${text.slice(0, max - 3)}...`;
}

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

function sanitizedUrl(value) {
  if (typeof value !== "string") return sanitizeSessionValue(value);
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
    return bounded(parsed.toString(), MAX_STRING_CHARS);
  } catch {
    return bounded(value, MAX_STRING_CHARS);
  }
}

export function sanitizeSessionValue(value, depth = 0, seen = new WeakSet()) {
  if (value === null || value === undefined) return value ?? null;
  if (typeof value === "string") return bounded(value, MAX_STRING_CHARS);
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
        .map((item) => sanitizeSessionValue(item, depth + 1, seen));
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
      } else result[key] = sanitizeSessionValue(item, depth + 1, seen);
    }
    return result;
  } finally {
    seen.delete(value);
  }
}

function iso(value) {
  const date = value instanceof Date ? value : new Date(value);
  return Number.isNaN(date.getTime()) ? null : date.toISOString();
}

function summary(info) {
  return {
    id: info.id,
    name: info.name ?? null,
    createdAt: iso(info.created),
    modifiedAt: iso(info.modified),
    messageCount: info.messageCount,
    firstMessage: bounded(info.firstMessage, 500),
  };
}

export class SessionHistory {
  constructor({ workspaceDir, sessionDir }) {
    this.workspaceDir = workspaceDir;
    this.sessionDir = sessionDir;
  }

  async #sessions() {
    const sessions = await SessionManager.list(
      this.workspaceDir,
      this.sessionDir,
    );
    return sessions.sort((left, right) => {
      const modified = right.modified.getTime() - left.modified.getTime();
      return modified || left.id.localeCompare(right.id);
    });
  }

  async list({ limit = 50, cursor = null, query = "" } = {}) {
    const pageSize = Math.max(1, Math.min(MAX_LIST_LIMIT, Number(limit) || 50));
    const needle = bounded(query, 200).trim().toLocaleLowerCase();
    const filtered = (await this.#sessions()).filter((info) => {
      if (!needle) return true;
      return [info.id, info.name, info.firstMessage]
        .filter(Boolean)
        .some((value) => String(value).toLocaleLowerCase().includes(needle));
    });
    const cursorIndex = cursor
      ? filtered.findIndex((info) => info.id === cursor)
      : -1;
    const start = cursor ? (cursorIndex < 0 ? filtered.length : cursorIndex + 1) : 0;
    const selected = filtered.slice(start, start + pageSize);
    const hasMore = start + selected.length < filtered.length;
    return {
      items: selected.map(summary),
      total: filtered.length,
      nextCursor: hasMore ? selected.at(-1)?.id ?? null : null,
    };
  }

  async get(sessionId) {
    if (!IDENTIFIER_RE.test(sessionId ?? "")) return null;
    const info = (await this.#sessions()).find((item) => item.id === sessionId);
    if (!info) return null;
    let manager;
    try {
      manager = SessionManager.open(
        info.path,
        this.sessionDir,
        this.workspaceDir,
      );
    } catch {
      return null;
    }
    return {
      ...summary(info),
      header: sanitizeSessionValue(manager.getHeader()),
      leafId: manager.getLeafId(),
      entries: manager.getEntries().map((entry) => sanitizeSessionValue(entry)),
    };
  }
}
