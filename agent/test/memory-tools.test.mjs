import assert from "node:assert/strict";
import test from "node:test";

import { createMemoryTools } from "../src/memory-tools.mjs";

function jsonResponse(payload, status = 200) {
  return new Response(JSON.stringify(payload), {
    status,
    headers: { "content-type": "application/json" },
  });
}

function fixture(handler) {
  const calls = [];
  const tools = createMemoryTools({
    baseUrl: "http://memory.internal:8888",
    access: {
      bankId: "telegram:chat:-1001",
      references: [
        {
          memoryId: "memory-1",
          documentId: "telegram:thread:-1001:41",
          chunkId: "chunk-1",
        },
      ],
    },
    fetchImpl: async (url, options) => {
      calls.push({ url, options });
      return handler(url, options, calls.length);
    },
  });
  return { byName: new Map(tools.map((tool) => [tool.name, tool])), calls };
}

test("reflect uses fixed bank and budget and can run only once", async () => {
  const app = fixture((_url, options) => {
    const body = JSON.parse(options.body);
    assert.deepEqual(body, {
      query: "Who is Rocket and what changed?",
      budget: "mid",
      max_tokens: 1_500,
      fact_types: ["world", "experience", "observation"],
      include: {
        facts: { max_tokens: 2_000 },
        tool_calls: { max_tokens: 750 },
      },
    });
    return jsonResponse({
      text: "Rocket is Alice; her plan changed yesterday.",
      based_on: { memories: [{ id: "memory-2" }] },
    });
  });
  const reflect = app.byName.get("memory_reflect");

  const result = await reflect.execute("call-1", {
    question: "Who is Rocket and what changed?",
  });
  assert.match(app.calls[0].url, /banks\/telegram%3Achat%3A-1001\/reflect$/);
  assert.match(result.content[0].text, /memory-2/);
  await assert.rejects(
    reflect.execute("call-2", { question: "Try again" }),
    /limit reached/,
  );
  assert.equal(app.calls.length, 1);
});

test("reflect accepts rich local payloads while bounding agent-visible output", async () => {
  const app = fixture(() =>
    jsonResponse({
      text: `Current answer ${"x".repeat(300_000)}`,
      based_on: { memories: [{ id: "memory-2" }] },
    }),
  );

  const result = await app.byName
    .get("memory_reflect")
    .execute("call-1", { question: "What changed?" });

  assert.match(result.content[0].text, /Current answer/);
  assert(result.content[0].text.length < 7_000);
  assert.match(result.content[0].text, /memory-2/);
});

test("reflect rejects responses above the local transport ceiling", async () => {
  const app = fixture(() => new Response("x".repeat(1024 * 1024 + 1)));

  const result = await app.byName
    .get("memory_reflect")
    .execute("call-1", { question: "What changed?" });

  assert.match(result.content[0].text, /initial recalled context/);
  assert.equal(result.details.unavailable, true);
});

test("reflect service failure returns a bounded recall fallback", async () => {
  const app = fixture(() => jsonResponse({ error: "busy" }, 503));

  const result = await app.byName
    .get("memory_reflect")
    .execute("call-1", { question: "What changed?" });

  assert.match(result.content[0].text, /initial recalled context/);
  assert.equal(result.details.unavailable, true);
});

test("source retrieval accepts only returned IDs and verifies scoped chunks", async () => {
  const app = fixture((url) => {
    if (url.includes("/memories/memory-1")) {
      return jsonResponse({
        id: "memory-1",
        document_id: "telegram:thread:-1001:41",
        chunk_id: "chunk-1",
      });
    }
    if (url.includes("/chunks?limit=20")) {
      return jsonResponse({
        items: [
          {
            chunk_id: "chunk-1",
            document_id: "telegram:thread:-1001:41",
            bank_id: "telegram:chat:-1001",
            chunk_text: "Alice directly said the launch moved to Friday.",
          },
        ],
      });
    }
    throw new Error(`unexpected URL ${url}`);
  });
  const sources = app.byName.get("memory_get_sources");

  const result = await sources.execute("call-1", { memoryIds: ["memory-1"] });
  assert.match(result.content[0].text, /Alice directly said/);
  assert(
    app.calls.every(({ url }) =>
      url.includes("/banks/telegram%3Achat%3A-1001/"),
    ),
  );
  await assert.rejects(
    sources.execute("call-2", { memoryIds: ["foreign-memory"] }),
    /not returned in this run/,
  );
});

test("source service failure preserves prior evidence as a bounded fallback", async () => {
  const app = fixture(() => jsonResponse({ error: "busy" }, 503));

  const result = await app.byName
    .get("memory_get_sources")
    .execute("call-1", { memoryIds: ["memory-1"] });

  assert.match(result.content[0].text, /prior recalled or reflected evidence/);
  assert(result.content[0].text.length < 500);
});

test("memory tools are absent without a host capability", () => {
  assert.deepEqual(
    createMemoryTools({ baseUrl: "http://memory", access: null }),
    [],
  );
});
