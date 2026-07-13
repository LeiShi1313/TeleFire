import assert from "node:assert/strict";
import test from "node:test";

import { createAgentServer } from "../src/http-service.mjs";

const validRun = {
  runId: "11111111-1111-4111-8111-111111111111",
  sessionId: null,
  parentEntryId: null,
  prompt: "Calculate 6 * 7",
  context: [],
  systemPrompt: "Answer concisely.",
  toolPolicy: "delegated",
};

async function listen(engine) {
  const server = createAgentServer({
    engine,
    token: "test-agent-token-that-is-long-enough",
    logger: { info() {}, error() {} },
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  const { port } = server.address();
  return {
    server,
    baseUrl: `http://127.0.0.1:${port}`,
    close: () => new Promise((resolve) => server.close(resolve)),
  };
}

test("streams a run as NDJSON", async () => {
  const engine = {
    async *run(request) {
      assert.deepEqual(request, validRun);
      yield { type: "run_started", runId: request.runId, sessionId: "session-1" };
      yield { type: "text_delta", delta: "42", reset: true };
      yield {
        type: "run_completed",
        sessionId: "session-1",
        entryId: "entry-1",
        answer: "42",
      };
    },
    async cancel() {
      return false;
    },
  };
  const app = await listen(engine);
  try {
    const response = await fetch(`${app.baseUrl}/v1/runs`, {
      method: "POST",
      headers: {
        "content-type": "application/json",
        authorization: "Bearer test-agent-token-that-is-long-enough",
      },
      body: JSON.stringify(validRun),
    });
    assert.equal(response.status, 200);
    assert.match(response.headers.get("content-type"), /application\/x-ndjson/);
    const events = (await response.text())
      .trim()
      .split("\n")
      .map((line) => JSON.parse(line));
    assert.equal(events.at(-1).type, "run_completed");
    assert.equal(events.at(-1).entryId, "entry-1");
  } finally {
    await app.close();
  }
});

test("rejects invalid run input before invoking the engine", async () => {
  let called = false;
  const app = await listen({
    async *run() {
      called = true;
    },
    async cancel() {
      return false;
    },
  });
  try {
    const response = await fetch(`${app.baseUrl}/v1/runs`, {
      method: "POST",
      headers: {
        "content-type": "application/json",
        authorization: "Bearer test-agent-token-that-is-long-enough",
      },
      body: JSON.stringify({ ...validRun, prompt: "", toolPolicy: "owner-ish" }),
    });
    assert.equal(response.status, 400);
    assert.equal(called, false);
    assert.deepEqual(await response.json(), {
      error: { code: "INVALID_REQUEST", message: "Invalid run request" },
    });
  } finally {
    await app.close();
  }
});

test("accepts bounded host-pinned memory access and rejects bank injection", async () => {
  let received;
  const app = await listen({
    async *run(request) {
      received = request;
      yield { type: "run_completed", sessionId: "s", entryId: "e", answer: "ok" };
    },
    async cancel() {
      return false;
    },
  });
  const memoryAccess = {
    bankId: "telegram:chat:-1001",
    references: [
      {
        memoryId: "memory-1",
        documentId: "telegram:thread:-1001:41",
        chunkId: "telegram:chat:-1001_telegram:thread:-1001:41_0",
      },
    ],
  };
  try {
    const accepted = await fetch(`${app.baseUrl}/v1/runs`, {
      method: "POST",
      headers: {
        "content-type": "application/json",
        authorization: "Bearer test-agent-token-that-is-long-enough",
      },
      body: JSON.stringify({ ...validRun, memoryAccess }),
    });
    assert.equal(accepted.status, 200);
    await accepted.text();
    assert.deepEqual(received.memoryAccess, memoryAccess);

    const rejected = await fetch(`${app.baseUrl}/v1/runs`, {
      method: "POST",
      headers: {
        "content-type": "application/json",
        authorization: "Bearer test-agent-token-that-is-long-enough",
      },
      body: JSON.stringify({
        ...validRun,
        memoryAccess: { ...memoryAccess, bankId: "../../other-bank" },
      }),
    });
    assert.equal(rejected.status, 400);

    const rejectedChunk = await fetch(`${app.baseUrl}/v1/runs`, {
      method: "POST",
      headers: {
        "content-type": "application/json",
        authorization: "Bearer test-agent-token-that-is-long-enough",
      },
      body: JSON.stringify({
        ...validRun,
        memoryAccess: {
          ...memoryAccess,
          references: [{ ...memoryAccess.references[0], chunkId: "../other" }],
        },
      }),
    });
    assert.equal(rejectedChunk.status, 400);
  } finally {
    await app.close();
  }
});

test("cancels an active run by its caller-provided id", async () => {
  let cancelled;
  const app = await listen({
    async *run() {},
    async cancel(runId) {
      cancelled = runId;
      return true;
    },
  });
  try {
    const response = await fetch(
      `${app.baseUrl}/v1/runs/${validRun.runId}/cancel`,
      {
        method: "POST",
        headers: {
          authorization: "Bearer test-agent-token-that-is-long-enough",
        },
      },
    );
    assert.equal(response.status, 200);
    assert.deepEqual(await response.json(), { cancelled: true });
    assert.equal(cancelled, validRun.runId);
  } finally {
    await app.close();
  }
});

test("rejects unauthenticated run and cancellation requests", async () => {
  let called = false;
  const app = await listen({
    async *run() {
      called = true;
    },
    async cancel() {
      called = true;
      return true;
    },
  });
  try {
    const run = await fetch(`${app.baseUrl}/v1/runs`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(validRun),
    });
    const cancel = await fetch(
      `${app.baseUrl}/v1/runs/${validRun.runId}/cancel`,
      { method: "POST" },
    );
    assert.equal(run.status, 401);
    assert.equal(cancel.status, 401);
    assert.equal(called, false);
  } finally {
    await app.close();
  }
});

test("health reveals no provider or credential details", async () => {
  const app = await listen({
    async *run() {},
    async cancel() {
      return false;
    },
  });
  try {
    const response = await fetch(`${app.baseUrl}/health`);
    assert.equal(response.status, 200);
    assert.deepEqual(await response.json(), { status: "ok" });
  } finally {
    await app.close();
  }
});

test("describes one bounded attachment through the authenticated API", async () => {
  let received;
  const app = await listen({
    async *run() {},
    async cancel() {
      return false;
    },
    async describeAttachment(request) {
      received = request;
      return "Description: a red square.\nVisible text: none.";
    },
  });
  try {
    const response = await fetch(`${app.baseUrl}/v1/attachments/describe`, {
      method: "POST",
      headers: {
        "content-type": "application/json",
        authorization: "Bearer test-agent-token-that-is-long-enough",
      },
      body: JSON.stringify({
        kind: "image",
        mimeType: "image/jpeg",
        filename: "sample.jpg",
        data: Buffer.from("bounded-image").toString("base64"),
      }),
    });

    assert.equal(response.status, 200);
    assert.deepEqual(await response.json(), {
      description: "Description: a red square.\nVisible text: none.",
    });
    assert.equal(received.kind, "image");
    assert.equal(received.mimeType, "image/jpeg");
    assert.equal(received.filename, "sample.jpg");
    assert.deepEqual(received.data, Buffer.from("bounded-image"));
  } finally {
    await app.close();
  }
});

test("rejects invalid or unauthenticated attachment analysis", async () => {
  let called = false;
  const app = await listen({
    async *run() {},
    async cancel() {
      return false;
    },
    async describeAttachment() {
      called = true;
      return "unused";
    },
  });
  try {
    const invalid = await fetch(`${app.baseUrl}/v1/attachments/describe`, {
      method: "POST",
      headers: {
        "content-type": "application/json",
        authorization: "Bearer test-agent-token-that-is-long-enough",
      },
      body: JSON.stringify({ kind: "audio", text: "not supported" }),
    });
    const unauthenticated = await fetch(
      `${app.baseUrl}/v1/attachments/describe`,
      {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          kind: "text",
          mimeType: "text/plain",
          text: "private document",
        }),
      },
    );

    assert.equal(invalid.status, 400);
    assert.equal(unauthenticated.status, 401);
    assert.equal(called, false);
  } finally {
    await app.close();
  }
});

test("rejects an invalid attachment description from the engine", async () => {
  const app = await listen({
    async *run() {},
    async cancel() {
      return false;
    },
    async describeAttachment() {
      return "x".repeat(4_001);
    },
  });
  try {
    const response = await fetch(`${app.baseUrl}/v1/attachments/describe`, {
      method: "POST",
      headers: {
        "content-type": "application/json",
        authorization: "Bearer test-agent-token-that-is-long-enough",
      },
      body: JSON.stringify({
        kind: "text",
        mimeType: "text/plain",
        text: "bounded source",
      }),
    });

    assert.equal(response.status, 502);
    assert.deepEqual(await response.json(), {
      error: { code: "ANALYSIS_FAILED", message: "Attachment analysis failed" },
    });
  } finally {
    await app.close();
  }
});
