import assert from "node:assert/strict";
import test from "node:test";

import {
  buildMemoryQueries,
  retrieveMemoryContext,
} from "../src/memory-context.mjs";

function response(results, status = 200) {
  return new Response(JSON.stringify({ results }), {
    status,
    headers: { "content-type": "application/json" },
  });
}

test("builds chat-agnostic unanchored and identity-anchored recall queries", () => {
  const queries = buildMemoryQueries({
    prompt: "What did Richard say?",
    context: [
      { kind: "reference", text: "Earlier conversation about telecom pricing." },
    ],
    memory: {
      scopeId: "workspace:engineering",
      anchors: [{ id: "person:alice", label: "Alice" }],
    },
  });

  assert.equal(queries.length, 2);
  assert.match(queries[0], /What did Richard say/);
  assert.match(queries[0], /Earlier conversation/);
  assert.doesNotMatch(queries[0], /person:alice/);
  assert.match(queries[1], /Identity anchors/);
  assert.match(queries[1], /Alice \(person:alice\)/);
});

test("preserves identity anchors when reference context fills the query budget", () => {
  const queries = buildMemoryQueries({
    prompt: "Who does this refer to?",
    context: [{ kind: "reference", text: "x".repeat(16_000) }],
    memory: {
      scopeId: "workspace:engineering",
      anchors: [{ id: "person:alice", label: "Alice" }],
    },
  });

  assert.equal(queries.length, 2);
  assert(queries[1].length <= 8_000);
  assert.match(queries[1], /Alice \(person:alice\)$/);
});

test("recalls query variants concurrently and merges their evidence by rank", async () => {
  const calls = [];
  let active = 0;
  let peak = 0;
  const fetchImpl = async (url, options) => {
    calls.push({ url, body: JSON.parse(options.body) });
    active += 1;
    peak = Math.max(peak, active);
    await new Promise((resolve) => setTimeout(resolve, 5));
    active -= 1;
    const anchored = options.body.includes("Identity anchors");
    return response(
      anchored
        ? [
            {
              id: "anchor-1",
              text: "Alice is also known as Rocket.",
              type: "world",
              entities: ["Alice", "person:alice"],
              document_id: "conversation:9",
              chunk_id: "chunk-9",
            },
            { id: "shared", text: "Shared evidence.", entities: [] },
          ]
        : [
            {
              id: "named-1",
              text: "Richard favors lower telecom prices.",
              type: "world",
              entities: ["Richard"],
              document_id: "conversation:7",
              chunk_id: "chunk-7",
            },
            { id: "shared", text: "Shared evidence.", entities: [] },
          ],
    );
  };

  const result = await retrieveMemoryContext({
    baseUrl: "http://memory.internal:8888",
    prompt: "What did Richard say?",
    context: [{ kind: "reference", text: "A telecom discussion." }],
    memory: {
      scopeId: "workspace:engineering",
      anchors: [{ id: "person:alice", label: "Alice" }],
    },
    fetchImpl,
  });

  assert.equal(calls.length, 2);
  assert.equal(peak, 2);
  assert(
    calls.every(({ url }) =>
      url.endsWith(
        "/v1/default/banks/workspace%3Aengineering/memories/recall",
      ),
    ),
  );
  assert.deepEqual(
    result.memories.map((item) => item.id),
    ["named-1", "anchor-1", "shared"],
  );
  assert.match(result.context, /Richard favors lower telecom prices/);
  assert.match(result.context, /Alice is also known as Rocket/);
  assert.deepEqual(result.access, {
    bankId: "workspace:engineering",
    references: [
      {
        memoryId: "named-1",
        documentId: "conversation:7",
        chunkId: "chunk-7",
      },
      {
        memoryId: "anchor-1",
        documentId: "conversation:9",
        chunkId: "chunk-9",
      },
      { memoryId: "shared", documentId: null, chunkId: null },
    ],
  });
});

test("keeps a source-capable reference when recalled text exceeds the prompt budget", async () => {
  const result = await retrieveMemoryContext({
    baseUrl: "http://memory.internal:8888",
    prompt: "What is the exact detail?",
    context: [],
    memory: { scopeId: "workspace:engineering", anchors: [] },
    fetchImpl: async () =>
      response([
        {
          id: "memory-large",
          text: `Important prefix ${"x".repeat(6_000)}`,
          entities: ["Alice"],
          document_id: "conversation:large",
          chunk_id: "chunk-large",
        },
      ]),
  });

  assert(result.context.length <= 4_000);
  assert.match(result.context, /Important prefix/);
  assert.match(result.context, /memory_id: memory-large/);
  assert.equal(result.access.references[0].memoryId, "memory-large");
});

test("keeps the surviving recall variant when the other one fails", async () => {
  const result = await retrieveMemoryContext({
    baseUrl: "http://memory.internal:8888",
    prompt: "Who is Rocket?",
    context: [],
    memory: {
      scopeId: "workspace:engineering",
      anchors: [{ id: "person:alice", label: "Alice" }],
    },
    fetchImpl: async (_url, options) =>
      options.body.includes("Identity anchors")
        ? response([{ id: "memory-1", text: "Rocket is Alice.", entities: [] }])
        : response([], 503),
  });

  assert.deepEqual(result.memories.map((item) => item.id), ["memory-1"]);
  assert.match(result.context, /Rocket is Alice/);
});

test("disables memory tools when every initial recall attempt fails", async () => {
  const result = await retrieveMemoryContext({
    baseUrl: "http://memory.internal:8888",
    prompt: "Who is Rocket?",
    context: [],
    memory: {
      scopeId: "workspace:engineering",
      anchors: [{ id: "person:alice", label: "Alice" }],
    },
    fetchImpl: async () => response([], 503),
  });

  assert.equal(result.context, "");
  assert.equal(result.access, null);
});

test("keeps reflection available after a successful empty recall", async () => {
  const result = await retrieveMemoryContext({
    baseUrl: "http://memory.internal:8888",
    prompt: "Who is Rocket?",
    context: [],
    memory: { scopeId: "workspace:engineering", anchors: [] },
    fetchImpl: async () => response([]),
  });

  assert.deepEqual(result.access, {
    bankId: "workspace:engineering",
    references: [],
  });
});

test("observes the complete initial recall HTTP exchange", async () => {
  const observed = [];
  const payload = {
    results: [
      {
        id: "memory-1",
        text: "Alice owns deployment.",
        entities: ["Alice"],
      },
    ],
  };

  await retrieveMemoryContext({
    baseUrl: "http://memory.internal:8888",
    prompt: "Who owns deployment?",
    context: [],
    memory: { scopeId: "workspace:engineering", anchors: [] },
    fetchImpl: async () =>
      new Response(JSON.stringify(payload), {
        status: 200,
        headers: { "content-type": "application/json" },
      }),
    observe: async (event) => observed.push(event),
  });

  assert.deepEqual(observed.map((event) => event.type), [
    "memory.http.request",
    "memory.http.response",
  ]);
  assert.equal(observed[0].data.operation, "recall");
  assert.equal(observed[0].data.variant, "unanchored");
  assert.match(observed[0].data.exchangeId, /^[0-9a-f-]{36}$/);
  assert.equal(observed[1].data.exchangeId, observed[0].data.exchangeId);
  assert.deepEqual(observed[0].data.request, {
    method: "POST",
    url: "http://memory.internal:8888/v1/default/banks/workspace%3Aengineering/memories/recall",
    body: {
      query: "Current request: Who owns deployment?",
      budget: "mid",
      max_tokens: 2_000,
      types: ["world", "experience", "observation"],
      include: {
        entities: { max_tokens: 500 },
        source_facts: { max_tokens: 750 },
      },
    },
  });
  assert.equal(observed[1].data.response.status, 200);
  assert.equal(observed[1].data.response.ok, true);
  assert.equal(observed[1].data.response.bodyBytes, Buffer.byteLength(JSON.stringify(payload)));
  assert.deepEqual(observed[1].data.response.body, payload);
  assert(Number.isInteger(observed[1].data.response.durationMs));
});
