import { isIP } from "node:net";

const FORBIDDEN_HOSTS = new Set([
  "github.com",
  "www.github.com",
  "youtu.be",
  "youtube.com",
  "www.youtube.com",
  "m.youtube.com",
  "music.youtube.com",
]);
const RECENCY_FILTERS = new Set(["day", "week", "month", "year"]);

function isPrivateIPv4(hostname) {
  const parts = hostname.split(".").map(Number);
  if (parts.length !== 4 || parts.some((part) => !Number.isInteger(part))) {
    return false;
  }
  const [a, b] = parts;
  return (
    a === 0 ||
    a === 10 ||
    a === 127 ||
    (a === 169 && b === 254) ||
    (a === 172 && b >= 16 && b <= 31) ||
    (a === 192 && b === 168) ||
    a >= 224
  );
}

function assertAllowedUrl(raw) {
  if (typeof raw !== "string" || raw.length > 2_048) {
    throw new Error("URL is not allowed");
  }
  let url;
  try {
    url = new URL(raw);
  } catch {
    throw new Error("URL is not allowed");
  }
  if (!new Set(["http:", "https:"]).has(url.protocol)) {
    throw new Error("URL is not allowed");
  }
  if (url.username || url.password) {
    throw new Error("URL credentials are not allowed");
  }
  const hostname = url.hostname.toLowerCase().replace(/\.$/, "");
  if (
    hostname === "localhost" ||
    hostname.endsWith(".localhost") ||
    hostname.endsWith(".local") ||
    FORBIDDEN_HOSTS.has(hostname) ||
    (isIP(hostname) === 4 && isPrivateIPv4(hostname)) ||
    (isIP(hostname) === 6 &&
      (hostname === "::1" || hostname.startsWith("fc") || hostname.startsWith("fd") || hostname.startsWith("fe80")))
  ) {
    throw new Error("URL host is not allowed");
  }
  return url.toString();
}

function normalizeQueries(params) {
  const source = Array.isArray(params.queries)
    ? params.queries
    : params.query === undefined
      ? []
      : [params.query];
  const queries = source
    .filter((query) => typeof query === "string")
    .map((query) => query.trim().slice(0, 500))
    .filter(Boolean)
    .slice(0, 4);
  if (queries.length === 0) throw new Error("At least one search query is required");
  return queries;
}

function constrainSearch(definition) {
  return {
    ...definition,
    description:
      "Search the public web through Exa. Returns raw cited results for the agent to synthesize.",
    async execute(toolCallId, params, signal, onUpdate, ctx) {
      const queries = normalizeQueries(params ?? {});
      const safe = {
        ...(Array.isArray(params?.queries)
          ? { queries }
          : { query: queries[0] }),
        provider: "exa",
        workflow: "none",
        includeContent: false,
      };
      if (Number.isInteger(params?.numResults)) {
        safe.numResults = Math.min(10, Math.max(1, params.numResults));
      }
      if (RECENCY_FILTERS.has(params?.recencyFilter)) {
        safe.recencyFilter = params.recencyFilter;
      }
      if (Array.isArray(params?.domainFilter)) {
        safe.domainFilter = params.domainFilter
          .filter((value) => typeof value === "string")
          .map((value) => value.trim().slice(0, 253))
          .filter(Boolean)
          .slice(0, 10);
      }
      return await definition.execute(toolCallId, safe, signal, onUpdate, ctx);
    },
  };
}

function constrainFetch(definition) {
  return {
    ...definition,
    description:
      "Fetch and extract readable content from up to three public HTTP or HTTPS pages.",
    async execute(toolCallId, params, signal, onUpdate, ctx) {
      const supplied = Array.isArray(params?.urls)
        ? params.urls
        : params?.url === undefined
          ? []
          : [params.url];
      if (supplied.length === 0 || supplied.length > 3) {
        throw new Error("URL count is not allowed");
      }
      const urls = supplied.map(assertAllowedUrl);
      const safe = Array.isArray(params?.urls) ? { urls } : { url: urls[0] };
      return await definition.execute(toolCallId, safe, signal, onUpdate, ctx);
    },
  };
}

export function constrainWebTools(definitions) {
  const byName = new Map(definitions.map((definition) => [definition.name, definition]));
  const search = byName.get("web_search");
  const fetchContent = byName.get("fetch_content");
  if (!search || !fetchContent) {
    throw new Error("pi-web-access did not register the required tools");
  }
  return [constrainSearch(search), constrainFetch(fetchContent)];
}
