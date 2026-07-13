import { lookup as dnsLookup } from "node:dns/promises";
import { BlockList, isIP } from "node:net";

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
const REDIRECT_STATUSES = new Set([301, 302, 303, 307, 308]);
const MAX_REDIRECTS = 5;
const BLOCKED_HOST_SUFFIXES = [".internal", ".local", ".localhost", ".home.arpa"];
const BLOCKED_IPV4 = blockList("ipv4", [
  ["0.0.0.0", 8],
  ["10.0.0.0", 8],
  ["100.64.0.0", 10],
  ["127.0.0.0", 8],
  ["169.254.0.0", 16],
  ["172.16.0.0", 12],
  ["192.0.0.0", 24],
  ["192.0.2.0", 24],
  ["192.31.196.0", 24],
  ["192.52.193.0", 24],
  ["192.88.99.0", 24],
  ["192.168.0.0", 16],
  ["192.175.48.0", 24],
  ["198.18.0.0", 15],
  ["198.51.100.0", 24],
  ["203.0.113.0", 24],
  ["224.0.0.0", 4],
  ["240.0.0.0", 4],
]);
const PUBLIC_IPV6 = blockList("ipv6", [["2000::", 3]]);
const BLOCKED_IPV6 = blockList("ipv6", [
  ["2001::", 23],
  ["2001:db8::", 32],
  ["2002::", 16],
  ["3fff::", 20],
]);

function blockList(family, ranges) {
  const list = new BlockList();
  for (const [network, prefix] of ranges) {
    list.addSubnet(network, prefix, family);
  }
  return list;
}

async function defaultLookup(hostname) {
  return await dnsLookup(hostname, { all: true, verbatim: true });
}

function normalizedHostname(hostname) {
  return hostname
    .toLowerCase()
    .replace(/^\[|\]$/g, "")
    .replace(/\.$/, "");
}

function isPrivateOrReservedAddress(address) {
  if (typeof address !== "string") return true;
  const normalized = normalizedHostname(address);
  const version = isIP(normalized);
  if (version === 4) return BLOCKED_IPV4.check(normalized, "ipv4");
  if (version === 6) {
    return (
      !PUBLIC_IPV6.check(normalized, "ipv6") ||
      BLOCKED_IPV6.check(normalized, "ipv6")
    );
  }
  return true;
}

function assertPublicAddress(address) {
  if (isPrivateOrReservedAddress(address)) {
    throw new Error("URL host is not allowed");
  }
}

async function assertAllowedUrl(raw, lookup) {
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
  const hostname = normalizedHostname(url.hostname);
  if (
    !hostname ||
    hostname === "localhost" ||
    BLOCKED_HOST_SUFFIXES.some((suffix) => hostname.endsWith(suffix)) ||
    FORBIDDEN_HOSTS.has(hostname) ||
    (isIP(hostname) === 0 && !hostname.includes("."))
  ) {
    throw new Error("URL host is not allowed");
  }

  if (isIP(hostname)) {
    assertPublicAddress(hostname);
    return url.toString();
  }

  let addresses;
  try {
    addresses = await lookup(hostname);
  } catch {
    throw new Error("URL host is not allowed");
  }
  if (!Array.isArray(addresses) || addresses.length === 0) {
    throw new Error("URL host is not allowed");
  }
  for (const result of addresses) assertPublicAddress(result?.address);
  return url.toString();
}

async function assertAllowedRedirectChain(raw, { lookup, fetch: fetchImpl, signal }) {
  let current = await assertAllowedUrl(raw, lookup);
  for (let redirects = 0; redirects <= MAX_REDIRECTS; redirects += 1) {
    const response = await fetchImpl(current, {
      method: "HEAD",
      redirect: "manual",
      signal,
    });
    try {
      if (!REDIRECT_STATUSES.has(response.status)) return current;
      const location = response.headers.get("location");
      if (!location || redirects === MAX_REDIRECTS) {
        throw new Error("URL redirect is not allowed");
      }
      current = await assertAllowedUrl(new URL(location, current).toString(), lookup);
    } finally {
      await response.body?.cancel().catch(() => {});
    }
  }
  throw new Error("URL redirect is not allowed");
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

function constrainFetch(definition, options) {
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
      const urls = await Promise.all(
        supplied.map((url) =>
          assertAllowedRedirectChain(url, { ...options, signal }),
        ),
      );
      const safe = Array.isArray(params?.urls) ? { urls } : { url: urls[0] };
      return await definition.execute(toolCallId, safe, signal, onUpdate, ctx);
    },
  };
}

export function constrainWebTools(
  definitions,
  { lookup = defaultLookup, fetch: fetchImpl = fetch } = {},
) {
  const byName = new Map(definitions.map((definition) => [definition.name, definition]));
  const search = byName.get("web_search");
  const fetchContent = byName.get("fetch_content");
  if (!search || !fetchContent) {
    throw new Error("pi-web-access did not register the required tools");
  }
  return [
    constrainSearch(search),
    constrainFetch(fetchContent, { lookup, fetch: fetchImpl }),
  ];
}
