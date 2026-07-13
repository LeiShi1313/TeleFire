import assert from "node:assert/strict";
import test from "node:test";

import { constrainWebTools } from "../src/web-tools.mjs";

const PUBLIC_ADDRESS = "93.184.216.34";

async function publicLookup() {
  return [{ address: PUBLIC_ADDRESS, family: 4 }];
}

async function noRedirectFetch() {
  return new Response(null, { status: 204 });
}

function tool(name, execute) {
  return {
    name,
    label: name,
    description: name,
    parameters: {},
    execute,
  };
}

test("forces raw Exa search and bounds query count", async () => {
  let received;
  const [search] = constrainWebTools([
    tool("web_search", async (_id, params) => {
      received = params;
      return { content: [{ type: "text", text: "ok" }], details: {} };
    }),
    tool("fetch_content", async () => ({ content: [], details: {} })),
  ]);

  await search.execute(
    "call-1",
    {
      queries: ["one", "two", "three", "four", "five"],
      provider: "openai",
      workflow: "summary-review",
    },
    undefined,
    undefined,
    {},
  );

  assert.deepEqual(received.queries, ["one", "two", "three", "four"]);
  assert.equal(received.provider, "exa");
  assert.equal(received.workflow, "none");
});

test("allows bounded public HTTP and HTTPS page retrieval", async () => {
  let received;
  const [, fetchContent] = constrainWebTools([
    tool("web_search", async () => ({ content: [], details: {} })),
    tool("fetch_content", async (_id, params) => {
      received = params;
      return { content: [{ type: "text", text: "page" }], details: {} };
    }),
  ], { lookup: publicLookup, fetch: noRedirectFetch });

  await fetchContent.execute(
    "call-2",
    { urls: ["https://example.com/a", "http://example.org/b"] },
    undefined,
    undefined,
    {},
  );

  assert.deepEqual(received, {
    urls: ["https://example.com/a", "http://example.org/b"],
  });
});

test("blocks internal hostnames, loopback, GitHub, and video retrieval", async () => {
  const [, fetchContent] = constrainWebTools([
    tool("web_search", async () => ({ content: [], details: {} })),
    tool("fetch_content", async () => ({ content: [], details: {} })),
  ], { lookup: publicLookup, fetch: noRedirectFetch });

  for (const url of [
    "file:///etc/passwd",
    "/etc/passwd",
    "http://hindsight:8888/v1/default/banks/",
    "http://localhost/private",
    "http://127.0.0.1/private",
    "http://192.0.2.1/reserved",
    "http://[::1]/private",
    "http://[2001:db8::1]/reserved",
    "https://github.com/owner/repo",
    "https://youtu.be/example",
  ]) {
    await assert.rejects(
      fetchContent.execute(
        "call-3",
        { url, forceClone: true, prompt: "inspect" },
        undefined,
        undefined,
        {},
      ),
      /not allowed/i,
    );
  }
});

test("blocks a hostname when any DNS answer is private", async () => {
  let delegated = false;
  const [, fetchContent] = constrainWebTools([
    tool("web_search", async () => ({ content: [], details: {} })),
    tool("fetch_content", async () => {
      delegated = true;
      return { content: [], details: {} };
    }),
  ], {
    lookup: async () => [
      { address: PUBLIC_ADDRESS, family: 4 },
      { address: "10.0.0.8", family: 4 },
    ],
    fetch: noRedirectFetch,
  });

  await assert.rejects(
    fetchContent.execute(
      "call-4",
      { url: "https://public-looking.example/page" },
      undefined,
      undefined,
      {},
    ),
    /not allowed/i,
  );
  assert.equal(delegated, false);
});

test("blocks redirects from a public URL to a private destination", async () => {
  const requested = [];
  let delegated = false;
  const [, fetchContent] = constrainWebTools([
    tool("web_search", async () => ({ content: [], details: {} })),
    tool("fetch_content", async () => {
      delegated = true;
      return { content: [], details: {} };
    }),
  ], {
    lookup: publicLookup,
    fetch: async (url) => {
      requested.push(url.toString());
      return new Response(null, {
        status: 302,
        headers: { location: "http://127.0.0.1/admin" },
      });
    },
  });

  await assert.rejects(
    fetchContent.execute(
      "call-5",
      { url: "https://example.com/start" },
      undefined,
      undefined,
      {},
    ),
    /not allowed/i,
  );
  assert.deepEqual(requested, ["https://example.com/start"]);
  assert.equal(delegated, false);
});

test("allows redirects between public destinations", async () => {
  const requested = [];
  let received;
  const [, fetchContent] = constrainWebTools([
    tool("web_search", async () => ({ content: [], details: {} })),
    tool("fetch_content", async (_id, params) => {
      received = params;
      return { content: [{ type: "text", text: "page" }], details: {} };
    }),
  ], {
    lookup: publicLookup,
    fetch: async (url) => {
      requested.push(url.toString());
      if (requested.length === 1) {
        return new Response(null, {
          status: 302,
          headers: { location: "https://www.example.org/final" },
        });
      }
      return new Response(null, { status: 204 });
    },
  });

  await fetchContent.execute(
    "call-6",
    { url: "https://example.com/start" },
    undefined,
    undefined,
    {},
  );

  assert.deepEqual(requested, [
    "https://example.com/start",
    "https://www.example.org/final",
  ]);
  assert.deepEqual(received, { url: "https://www.example.org/final" });
});
