import assert from "node:assert/strict";
import test from "node:test";

import { constrainWebTools } from "../src/web-tools.mjs";

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

test("allows bounded HTTP page retrieval", async () => {
  let received;
  const [, fetchContent] = constrainWebTools([
    tool("web_search", async () => ({ content: [], details: {} })),
    tool("fetch_content", async (_id, params) => {
      received = params;
      return { content: [{ type: "text", text: "page" }], details: {} };
    }),
  ]);

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

test("blocks local, GitHub, and video retrieval", async () => {
  const [, fetchContent] = constrainWebTools([
    tool("web_search", async () => ({ content: [], details: {} })),
    tool("fetch_content", async () => ({ content: [], details: {} })),
  ]);

  for (const url of [
    "file:///etc/passwd",
    "/etc/passwd",
    "http://127.0.0.1/private",
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
