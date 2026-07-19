import assert from "node:assert/strict";
import { createHash } from "node:crypto";
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

function memoryTarget(overrides = {}) {
  return {
    primaryBankId: "workspace:engineering",
    requester: { id: "chat:user:alice", label: "Alice", owner: false },
    grantedBankIds: [],
    participants: [],
    anchors: [],
    ...overrides,
  };
}

function bankTag(bankId) {
  return `telefire:bank-ref:${createHash("sha256").update(bankId).digest("hex")}`;
}

function directoryResult(bankId, name, text = `${name} is a knowledge source.`) {
  const tag = bankTag(bankId);
  return {
    id: `directory-${name}`,
    text,
    type: "world",
    entities: [],
    tags: [tag],
    metadata: {
      client: "telefire",
      source: "knowledge-directory",
      schema: "telefire.knowledge-directory.v1",
      bank_id: bankId,
      bank_ref: tag,
      source_name: name,
      source_platform: bankId.split(":")[0],
      source_kind: "group",
    },
  };
}

test("builds chat-agnostic unanchored and identity-anchored recall queries", () => {
  const queries = buildMemoryQueries({
    prompt: "What did Richard say?",
    context: [
      { kind: "reference", text: "Earlier conversation about telecom pricing." },
    ],
    memory: memoryTarget({
      anchors: [{ id: "person:alice", label: "Alice" }],
    }),
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
    memory: memoryTarget({
      anchors: [{ id: "person:alice", label: "Alice" }],
    }),
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
    memory: memoryTarget({
      anchors: [{ id: "person:alice", label: "Alice" }],
    }),
    fetchImpl,
  });

  assert.equal(calls.length, 3);
  assert.equal(peak, 3);
  assert(
    calls
      .filter(({ url }) => !url.includes("system%3Aknowledge-directory"))
      .every(({ url }) =>
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
    primaryBankId: "workspace:engineering",
    references: [
      {
        bankId: "workspace:engineering",
        memoryId: "named-1",
        documentId: "conversation:7",
        chunkId: "chunk-7",
      },
      {
        bankId: "workspace:engineering",
        memoryId: "anchor-1",
        documentId: "conversation:9",
        chunkId: "chunk-9",
      },
      {
        bankId: "workspace:engineering",
        memoryId: "shared",
        documentId: null,
        chunkId: null,
      },
    ],
    sourceCapabilities: [],
    directoryPolicy: {
      owner: false,
      allowedBankIds: ["workspace:engineering"],
    },
    participants: [],
  });
});

test("keeps a source-capable reference when recalled text exceeds the prompt budget", async () => {
  const result = await retrieveMemoryContext({
    baseUrl: "http://memory.internal:8888",
    prompt: "What is the exact detail?",
    context: [],
    memory: memoryTarget(),
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
    memory: memoryTarget({
      anchors: [{ id: "person:alice", label: "Alice" }],
    }),
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
    memory: memoryTarget({
      anchors: [{ id: "person:alice", label: "Alice" }],
    }),
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
    memory: memoryTarget(),
    fetchImpl: async () => response([]),
  });

  assert.deepEqual(result.access, {
    primaryBankId: "workspace:engineering",
    references: [],
    sourceCapabilities: [],
    directoryPolicy: {
      owner: false,
      allowedBankIds: ["workspace:engineering"],
    },
    participants: [],
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
    memory: memoryTarget(),
    fetchImpl: async () =>
      new Response(JSON.stringify(payload), {
        status: 200,
        headers: { "content-type": "application/json" },
      }),
    observe: async (event) => observed.push(event),
  });

  const recallEvents = observed.filter((event) => event.data.operation === "recall");
  assert.deepEqual(recallEvents.map((event) => event.type), [
    "memory.http.request",
    "memory.http.response",
  ]);
  assert.equal(recallEvents[0].data.variant, "unanchored");
  assert.match(recallEvents[0].data.exchangeId, /^[0-9a-f-]{36}$/);
  assert.equal(recallEvents[1].data.exchangeId, recallEvents[0].data.exchangeId);
  assert.deepEqual(recallEvents[0].data.request, {
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
  assert.equal(recallEvents[1].data.response.status, 200);
  assert.equal(recallEvents[1].data.response.ok, true);
  assert.equal(recallEvents[1].data.response.bodyBytes, Buffer.byteLength(JSON.stringify(payload)));
  assert.deepEqual(recallEvents[1].data.response.body, payload);
  assert(Number.isInteger(recallEvents[1].data.response.durationMs));
});

test("prefilters delegated directory recall and issues opaque source handles", async () => {
  const primaryBank = "telegram:chat:-1001";
  const grantedBank = "qq:group:686743769";
  const calls = [];
  const result = await retrieveMemoryContext({
    baseUrl: "http://memory.internal:8888",
    prompt: "Coder OT 群最近在聊什么？",
    context: [],
    memory: memoryTarget({
      primaryBankId: primaryBank,
      grantedBankIds: [grantedBank],
      participants: [
        {
          id: "chat:user:bob",
          label: "Bob",
          allowed: true,
          bankIds: [],
        },
      ],
    }),
    fetchImpl: async (url, options) => {
      const body = JSON.parse(options.body);
      calls.push({ url, body });
      if (url.includes("system%3Aknowledge-directory")) {
        return response([
          directoryResult(
            grantedBank,
            "Coder Offtopic",
            "Coder OT 群是一个中文技术群。",
          ),
        ]);
      }
      return response([]);
    },
  });

  const directoryCall = calls.find(({ url }) =>
    url.includes("system%3Aknowledge-directory"),
  );
  assert.deepEqual(directoryCall.body.tag_groups, [
    {
      or: [primaryBank, grantedBank].map((bankId) => ({
        tags: [bankTag(bankId)],
        match: "exact",
      })),
    },
  ]);
  assert.equal(result.access.sourceCapabilities.length, 1);
  assert.equal(result.access.sourceCapabilities[0].handle, "source_1");
  assert.equal(result.access.sourceCapabilities[0].bankId, grantedBank);
  assert.match(result.directoryContext, /source_1/);
  assert.match(result.directoryContext, /Coder Offtopic/);
  assert.match(result.directoryContext, /Bob.*no offered source access/i);
  assert.doesNotMatch(result.directoryContext, /qq:group:686743769/);
});

test("keeps same-name directory sources distinct instead of merging by label", async () => {
  const financeBank = "telegram:chat:-1002";
  const gamesBank = "telegram:chat:-1003";
  const result = await retrieveMemoryContext({
    baseUrl: "http://memory.internal:8888",
    prompt: "哪个晨报提到了股票，哪个提到了游戏？",
    context: [],
    memory: memoryTarget({
      grantedBankIds: [financeBank, gamesBank],
    }),
    fetchImpl: async (url) =>
      url.includes("system%3Aknowledge-directory")
        ? response([
            directoryResult(financeBank, "晨报", "财经晨报，关注股票和利率。"),
            directoryResult(gamesBank, "晨报", "游戏晨报，关注新作和电竞。"),
          ])
        : response([]),
  });

  assert.deepEqual(
    result.access.sourceCapabilities.map(({ handle, bankId }) => ({
      handle,
      bankId,
    })),
    [
      { handle: "source_1", bankId: financeBank },
      { handle: "source_2", bankId: gamesBank },
    ],
  );
  assert.match(result.directoryContext, /source_1.*财经晨报/s);
  assert.match(result.directoryContext, /source_2.*游戏晨报/s);
});

test("owner directory recall is unfiltered while malformed references fail closed", async () => {
  const calls = [];
  const result = await retrieveMemoryContext({
    baseUrl: "http://memory.internal:8888",
    prompt: "Find the news source",
    context: [],
    memory: memoryTarget({
      requester: { id: "chat:user:owner", label: "Owner", owner: true },
    }),
    fetchImpl: async (url, options) => {
      calls.push({ url, body: JSON.parse(options.body) });
      if (url.includes("system%3Aknowledge-directory")) {
        const malformed = directoryResult("telegram:chat:-1002", "News");
        malformed.metadata.client = "untrusted";
        return response([malformed]);
      }
      return response([{ id: "primary-1", text: "Primary evidence", entities: [] }]);
    },
  });

  const directoryCall = calls.find(({ url }) =>
    url.includes("system%3Aknowledge-directory"),
  );
  assert.equal(directoryCall.body.tag_groups, undefined);
  assert.deepEqual(result.memories.map((item) => item.id), ["primary-1"]);
  assert.deepEqual(result.access.sourceCapabilities, []);
});

test("participant access cannot revoke an owner's issued source capability", async () => {
  const sourceBank = "telegram:chat:-1002";
  const result = await retrieveMemoryContext({
    baseUrl: "http://memory.internal:8888",
    prompt: "What did Bob discuss in the other group today?",
    context: [],
    memory: memoryTarget({
      requester: { id: "chat:user:owner", label: "Owner", owner: true },
      participants: [
        {
          id: "chat:user:bob",
          label: "Bob",
          allowed: false,
          bankIds: [],
        },
      ],
    }),
    fetchImpl: async (url) =>
      url.includes("system%3Aknowledge-directory")
        ? response([
            directoryResult(
              sourceBank,
              "Other group",
              "Other group contains today's discussion.",
            ),
          ])
        : response([]),
  });

  assert.equal(result.access.directoryPolicy.owner, true);
  assert.equal(result.access.sourceCapabilities[0].handle, "source_1");
  assert.match(
    result.directoryContext,
    /every listed source handle is authorized for the current requester/i,
  );
  assert.match(
    result.directoryContext,
    /participant access.*does not revoke.*current requester/i,
  );
  assert.match(result.directoryContext, /Bob.*no offered source access/i);
});

test("skips an observation whose source fact was omitted by the recall budget", async () => {
  const validBank = "telegram:chat:-1002";
  const omittedBank = "telegram:chat:-1003";
  const unprovenBank = "telegram:chat:-1004";
  const omitted = directoryResult(omittedBank, "Omitted");
  omitted.metadata = {};
  omitted.source_fact_ids = ["source-fact-omitted-by-budget"];
  const unproven = directoryResult(unprovenBank, "Unproven");
  unproven.metadata = null;
  unproven.source_fact_ids = null;
  const result = await retrieveMemoryContext({
    baseUrl: "http://memory.internal:8888",
    prompt: "Find a source",
    context: [],
    memory: memoryTarget({
      requester: { id: "chat:user:owner", label: "Owner", owner: true },
    }),
    fetchImpl: async (url) =>
      url.includes("system%3Aknowledge-directory")
        ? response([directoryResult(validBank, "Valid"), omitted, unproven])
        : response([]),
  });

  assert.deepEqual(
    result.access.sourceCapabilities.map(({ bankId }) => bankId),
    [validBank],
  );
});

test("directory failure preserves primary recall and issues no source capability", async () => {
  const result = await retrieveMemoryContext({
    baseUrl: "http://memory.internal:8888",
    prompt: "What is known?",
    context: [],
    memory: memoryTarget({ grantedBankIds: ["telegram:chat:-1002"] }),
    fetchImpl: async (url) =>
      url.includes("system%3Aknowledge-directory")
        ? response([], 503)
        : response([{ id: "primary-1", text: "Known fact", entities: [] }]),
  });

  assert.match(result.context, /Known fact/);
  assert.equal(result.access.primaryBankId, "workspace:engineering");
  assert.deepEqual(result.access.sourceCapabilities, []);
});
