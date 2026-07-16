import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import test from "node:test";

import { createMemoryTools } from "../src/memory-tools.mjs";

function jsonResponse(payload, status = 200) {
  return new Response(JSON.stringify(payload), {
    status,
    headers: { "content-type": "application/json" },
  });
}

function fixture(handler, observe, accessOverrides = {}) {
  const calls = [];
  const tools = createMemoryTools({
    baseUrl: "http://memory.internal:8888",
    access: {
      primaryBankId: "telegram:chat:-1001",
      references: [
        {
          bankId: "telegram:chat:-1001",
          memoryId: "memory-1",
          documentId: "telegram:thread:-1001:41",
          chunkId: "chunk-1",
        },
      ],
      sourceCapabilities: [],
      directoryPolicy: {
        owner: false,
        allowedBankIds: ["telegram:chat:-1001"],
      },
      participants: [],
      ...accessOverrides,
    },
    fetchImpl: async (url, options) => {
      calls.push({ url, options });
      return handler(url, options, calls.length);
    },
    observe,
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

test("observes memory tool HTTP with tool-call correlation", async () => {
  const observed = [];
  const payload = {
    text: "Alice owns deployment.",
    based_on: { memories: [{ id: "memory-2" }] },
  };
  const app = fixture(
    () => jsonResponse(payload),
    async (event) => observed.push(event),
  );

  await app.byName
    .get("memory_reflect")
    .execute("call-reflect-1", { question: "Who owns deployment?" });

  assert.deepEqual(observed.map((event) => event.type), [
    "memory.http.request",
    "memory.http.response",
  ]);
  assert.equal(observed[0].data.operation, "reflect");
  assert.equal(observed[0].data.toolCallId, "call-reflect-1");
  assert.equal(observed[1].data.toolCallId, "call-reflect-1");
  assert.equal(observed[1].data.exchangeId, observed[0].data.exchangeId);
  assert.deepEqual(observed[0].data.request.body.query, "Who owns deployment?");
  assert.deepEqual(observed[1].data.response.body, payload);
});

test("queries only a host-issued source handle and exposes no bank ID to the model", async () => {
  const bankId = "qq:group:686743769";
  const app = fixture(
    (url, options) => {
      assert.match(url, /banks\/qq%3Agroup%3A686743769\/memories\/recall$/);
      assert.equal(JSON.parse(options.body).query, "最近在聊什么？");
      return jsonResponse({
        results: [
          {
            id: "cross-memory-1",
            text: "群里最近在讨论本地模型。",
            entities: [],
            document_id: "qq:thread:686743769:7",
          },
        ],
      });
    },
    null,
    {
      sourceCapabilities: [
        {
          handle: "source_1",
          bankId,
          displayName: "Coder Offtopic",
          platform: "qq",
          sourceKind: "group",
          evidence: [],
        },
      ],
      directoryPolicy: {
        owner: false,
        allowedBankIds: ["telegram:chat:-1001", bankId],
      },
    },
  );
  const querySource = app.byName.get("memory_query_source");

  const result = await querySource.execute("call-source-1", {
    reference: "source_1",
    query: "最近在聊什么？",
  });

  assert.match(result.content[0].text, /Coder Offtopic/);
  assert.match(result.content[0].text, /cross-memory-1/);
  assert.doesNotMatch(result.content[0].text, /qq:group:686743769/);
  assert.equal(result.details.bankId, bankId);
  await assert.rejects(
    querySource.execute("call-source-2", {
      reference: "source_999",
      query: "anything",
    }),
    /not issued by the host/,
  );
});

test("limits cross-bank traversal to two distinct consulted sources", async () => {
  const capabilities = [1, 2, 3].map((index) => ({
    handle: `source_${index}`,
    bankId: `chat:bank:${index}`,
    displayName: `Source ${index}`,
    platform: "chat",
    sourceKind: "group",
    evidence: [],
  }));
  const app = fixture(
    () => jsonResponse({ results: [] }),
    null,
    {
      sourceCapabilities: capabilities,
      directoryPolicy: {
        owner: true,
        allowedBankIds: null,
      },
    },
  );
  const tool = app.byName.get("memory_query_source");

  await tool.execute("call-1", { reference: "source_1", query: "one" });
  await tool.execute("call-2", { reference: "source_2", query: "two" });
  await tool.execute("call-3", { reference: "source_1", query: "again" });
  await assert.rejects(
    tool.execute("call-4", { reference: "source_3", query: "three" }),
    /consulted-source limit/,
  );
  assert.equal(app.calls.length, 3);
});

test("performs one extra filtered directory lookup and mints the next handle", async () => {
  const primary = "telegram:chat:-1001";
  const grant = "telegram:chat:-1002";
  const tag = `telefire:bank-ref:${createHash("sha256").update(grant).digest("hex")}`;
  const app = fixture(
    (url, options) => {
      assert.match(url, /banks\/system%3Aknowledge-directory\/memories\/recall$/);
      const body = JSON.parse(options.body);
      assert.deepEqual(body.tag_groups[0].or.map((item) => item.tags[0]), [
        `telefire:bank-ref:${createHash("sha256").update(primary).digest("hex")}`,
        tag,
      ]);
      return jsonResponse({
        results: [
          {
            id: "directory-memory-2",
            text: "这个群也叫 Arch 群。",
            entities: [],
            tags: [tag],
            metadata: {
              client: "telefire",
              source: "knowledge-directory",
              schema: "telefire.knowledge-directory.v1",
              bank_id: grant,
              bank_ref: tag,
              source_name: "Arch Linux 中文群",
              source_platform: "telegram",
              source_kind: "group",
            },
          },
        ],
      });
    },
    null,
    {
      directoryPolicy: {
        owner: false,
        allowedBankIds: [primary, grant],
      },
    },
  );
  const find = app.byName.get("memory_find_sources");

  const result = await find.execute("call-find-1", { query: "Arch 群是什么？" });

  assert.match(result.content[0].text, /source_1/);
  assert.match(result.content[0].text, /Arch Linux 中文群/);
  assert.equal(result.details.references[0].bankId, grant);
  await assert.rejects(
    find.execute("call-find-2", { query: "again" }),
    /directory lookup limit/,
  );
});
