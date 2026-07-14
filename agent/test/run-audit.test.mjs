import assert from "node:assert/strict";
import { appendFile, mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

import { RunAuditStore } from "../src/run-audit.mjs";

const RUN_ID = "11111111-1111-4111-8111-111111111111";

async function fixture() {
  const root = await mkdtemp(join(tmpdir(), "telefire-run-audit-"));
  return {
    root,
    store: new RunAuditStore(root),
    close: () => rm(root, { recursive: true, force: true }),
  };
}

test("records ordered append-only events and redacts credential-shaped fields", async () => {
  const app = await fixture();
  try {
    const audit = await app.store.start(RUN_ID);
    await Promise.all([
      audit.record("run.request", {
        prompt: "Who owns deploys?",
        sessionId: null,
        systemPrompt: "Answer carefully.",
        memory: { scopeId: "chat:engineering" },
        authorization: "Bearer secret",
        provider: { errorMessage: "provider credential detail" },
        callbackUrl: "https://user:pass@example.test/path?api_key=query-secret&view=full",
        image: {
          type: "image",
          mimeType: "image/png",
          data: "aW1hZ2UtcGF5bG9hZA==",
        },
      }),
      audit.record("memory.http.request", {
        exchangeId: "recall-plain",
        request: {
          method: "POST",
          url: "http://memory/v1/default/banks/chat/memories/recall",
          body: { query: "Who owns deploys?", apiKey: "secret-key" },
        },
      }),
    ]);
    await audit.record("session.opened", {
      sessionId: "session-1",
      parentEntryId: null,
    });
    await audit.record("run.completed", {
      sessionId: "session-1",
      entryId: "entry-1",
      answer: "Alice owns deploys.",
    });
    await audit.flush();

    const result = await app.store.get(RUN_ID);
    assert.equal(result.runId, RUN_ID);
    assert.deepEqual(result.events.map((event) => event.sequence), [1, 2, 3, 4]);
    assert.deepEqual(result.events.map((event) => event.type), [
      "run.request",
      "memory.http.request",
      "session.opened",
      "run.completed",
    ]);
    assert.equal(result.events[0].data.authorization, "[REDACTED]");
    assert.equal(result.events[0].data.provider.errorMessage, "[REDACTED]");
    assert.equal(
      result.events[0].data.callbackUrl,
      "https://example.test/path?api_key=%5BREDACTED%5D&view=full",
    );
    assert.deepEqual(result.events[0].data.image, {
      type: "image",
      mimeType: "image/png",
      sizeBytes: 13,
      data: "[OMITTED]",
    });
    assert.equal(
      result.events[1].data.request.body.apiKey,
      "[REDACTED]",
    );
    assert.doesNotMatch(
      JSON.stringify(result),
      /Bearer secret|secret-key|provider credential detail|query-secret|user:pass/,
    );
  } finally {
    await app.close();
  }
});

test("recovers complete events before a crash-truncated final line", async () => {
  const app = await fixture();
  try {
    const audit = await app.store.start(RUN_ID);
    await audit.record("run.request", { prompt: "Recover me" });
    await audit.flush();
    await appendFile(`${app.root}/${RUN_ID}.jsonl`, '{"version":1,"sequence":2');

    const result = await app.store.get(RUN_ID);
    assert.deepEqual(result.events.map((event) => event.type), ["run.request"]);
  } finally {
    await app.close();
  }
});

test("lists run summaries by session and reports terminal state", async () => {
  const app = await fixture();
  try {
    const first = await app.store.start(RUN_ID);
    await first.record("run.request", {
      prompt: "First run",
      sessionId: null,
      memory: { scopeId: "chat:engineering" },
    });
    await first.record("session.opened", { sessionId: "session-1" });
    await first.record("run.completed", {
      sessionId: "session-1",
      entryId: "entry-1",
    });
    await first.flush();

    const secondId = "22222222-2222-4222-8222-222222222222";
    const second = await app.store.start(secondId);
    await second.record("run.request", {
      prompt: "Second run",
      sessionId: "session-2",
    });
    await second.record("run.failed", { code: "PROVIDER_ERROR" });
    await second.flush();

    const page = await app.store.list({ limit: 20, sessionId: "session-1" });
    assert.equal(page.total, 1);
    assert.equal(page.items[0].runId, RUN_ID);
    assert.equal(page.items[0].sessionId, "session-1");
    assert.equal(page.items[0].entryId, "entry-1");
    assert.equal(page.items[0].status, "completed");
    assert.equal(page.items[0].memoryScopeId, "chat:engineering");
    assert.equal(page.items[0].eventCount, 3);

    const all = await app.store.list({ limit: 20 });
    assert.equal(all.total, 2);
    assert(all.items.some((item) => item.status === "failed"));
  } finally {
    await app.close();
  }
});

test("does not read audit paths for malformed or unknown run identities", async () => {
  const app = await fixture();
  try {
    assert.equal(await app.store.get("../../secret"), null);
    assert.equal(
      await app.store.get("33333333-3333-4333-8333-333333333333"),
      null,
    );
  } finally {
    await app.close();
  }
});
