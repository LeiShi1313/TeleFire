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

test("accepts a no-tools model run", async () => {
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
  try {
    const response = await fetch(`${app.baseUrl}/v1/runs`, {
      method: "POST",
      headers: {
        "content-type": "application/json",
        authorization: "Bearer test-agent-token-that-is-long-enough",
      },
      body: JSON.stringify({ ...validRun, toolPolicy: "none" }),
    });
    assert.equal(response.status, 200);
    await response.text();
    assert.equal(received.toolPolicy, "none");
  } finally {
    await app.close();
  }
});

test("accepts a bounded memory target and rejects scope injection", async () => {
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
  const memory = {
    primaryBankId: "telegram:chat:-1001",
    requester: {
      id: "telegram:user:40",
      label: "Alice",
      owner: false,
    },
    grantedBankIds: ["qq:group:686743769"],
    participants: [
      {
        id: "telegram:user:41",
        label: "Bob",
        allowed: true,
        bankIds: ["telegram:chat:-1002"],
      },
    ],
    query: "What does Alice prefer?",
    anchors: [
      {
        id: "telegram:user:40",
        label: "Alice",
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
      body: JSON.stringify({
        ...validRun,
        context: [{ kind: "reference", text: "Alice joined the discussion." }],
        memory,
        includeMemorySnapshot: true,
      }),
    });
    assert.equal(accepted.status, 200);
    await accepted.text();
    assert.deepEqual(received.memory, memory);
    assert.equal(received.includeMemorySnapshot, true);

    const rejectedSnapshotFlag = await fetch(`${app.baseUrl}/v1/runs`, {
      method: "POST",
      headers: {
        "content-type": "application/json",
        authorization: "Bearer test-agent-token-that-is-long-enough",
      },
      body: JSON.stringify({
        ...validRun,
        includeMemorySnapshot: "yes",
      }),
    });
    assert.equal(rejectedSnapshotFlag.status, 400);

    const rejected = await fetch(`${app.baseUrl}/v1/runs`, {
      method: "POST",
      headers: {
        "content-type": "application/json",
        authorization: "Bearer test-agent-token-that-is-long-enough",
      },
      body: JSON.stringify({
        ...validRun,
        memory: { ...memory, primaryBankId: "../../other-bank" },
      }),
    });
    assert.equal(rejected.status, 400);

    const rejectedNumericBank = await fetch(`${app.baseUrl}/v1/runs`, {
      method: "POST",
      headers: {
        "content-type": "application/json",
        authorization: "Bearer test-agent-token-that-is-long-enough",
      },
      body: JSON.stringify({
        ...validRun,
        memory: { ...memory, primaryBankId: 1001 },
      }),
    });
    assert.equal(rejectedNumericBank.status, 400);

    const rejectedInjectedReferences = await fetch(`${app.baseUrl}/v1/runs`, {
      method: "POST",
      headers: {
        "content-type": "application/json",
        authorization: "Bearer test-agent-token-that-is-long-enough",
      },
      body: JSON.stringify({
        ...validRun,
        memory: {
          ...memory,
          sourceCapabilities: [{ handle: "source_1" }],
        },
      }),
    });
    assert.equal(rejectedInjectedReferences.status, 400);

    const rejectedNumericRequester = await fetch(`${app.baseUrl}/v1/runs`, {
      method: "POST",
      headers: {
        "content-type": "application/json",
        authorization: "Bearer test-agent-token-that-is-long-enough",
      },
      body: JSON.stringify({
        ...validRun,
        memory: {
          ...memory,
          requester: { ...memory.requester, id: 40 },
        },
      }),
    });
    assert.equal(rejectedNumericRequester.status, 400);

    const rejectedParticipantGrants = await fetch(`${app.baseUrl}/v1/runs`, {
      method: "POST",
      headers: {
        "content-type": "application/json",
        authorization: "Bearer test-agent-token-that-is-long-enough",
      },
      body: JSON.stringify({
        ...validRun,
        memory: {
          ...memory,
          participants: [
            {
              id: "telegram:user:41",
              label: "Bob",
              allowed: false,
              bankIds: ["telegram:chat:-1002"],
            },
          ],
        },
      }),
    });
    assert.equal(rejectedParticipantGrants.status, 400);
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

test("serves authenticated session history and run audits", async () => {
  const calls = [];
  const engine = {
    async *run() {},
    async cancel() {
      return false;
    },
    async listSessions(options) {
      calls.push(["listSessions", options]);
      return {
        items: [
          {
            id: "session-1",
            name: "Deployment",
            messageCount: 4,
            firstMessage: "Inspect deployment",
          },
        ],
        total: 1,
        nextCursor: null,
      };
    },
    async getSession(sessionId) {
      calls.push(["getSession", sessionId]);
      return sessionId === "session-1"
        ? { id: sessionId, leafId: "entry-1", entries: [] }
        : null;
    },
    async listRunAudits(options) {
      calls.push(["listRunAudits", options]);
      return {
        items: [{ runId: validRun.runId, sessionId: "session-1" }],
        total: 1,
        nextCursor: null,
      };
    },
    async getRunAudit(runId) {
      calls.push(["getRunAudit", runId]);
      return runId === validRun.runId ? { runId, events: [] } : null;
    },
  };
  const app = await listen(engine);
  const headers = {
    authorization: "Bearer test-agent-token-that-is-long-enough",
  };
  try {
    const list = await fetch(
      `${app.baseUrl}/v1/sessions?limit=20&q=deploy&cursor=session-0`,
      { headers },
    );
    assert.equal(list.status, 200);
    assert.equal((await list.json()).items[0].id, "session-1");

    const session = await fetch(`${app.baseUrl}/v1/sessions/session-1`, {
      headers,
    });
    assert.equal(session.status, 200);
    assert.equal((await session.json()).leafId, "entry-1");

    const audits = await fetch(
      `${app.baseUrl}/v1/runs?limit=10&sessionId=session-1`,
      { headers },
    );
    assert.equal(audits.status, 200);
    assert.equal((await audits.json()).items[0].runId, validRun.runId);

    const audit = await fetch(
      `${app.baseUrl}/v1/runs/${validRun.runId}/audit`,
      { headers },
    );
    assert.equal(audit.status, 200);
    assert.equal((await audit.json()).runId, validRun.runId);

    assert.deepEqual(calls, [
      [
        "listSessions",
        { limit: 20, cursor: "session-0", query: "deploy" },
      ],
      ["getSession", "session-1"],
      [
        "listRunAudits",
        { limit: 10, cursor: null, sessionId: "session-1" },
      ],
      ["getRunAudit", validRun.runId],
    ]);
  } finally {
    await app.close();
  }
});

test("validates history queries and returns stable missing-resource errors", async () => {
  let called = false;
  const app = await listen({
    async *run() {},
    async cancel() {
      return false;
    },
    async listSessions() {
      called = true;
      return { items: [], total: 0, nextCursor: null };
    },
    async getSession() {
      return null;
    },
    async listRunAudits() {
      called = true;
      return { items: [], total: 0, nextCursor: null };
    },
    async getRunAudit() {
      return null;
    },
  });
  const headers = {
    authorization: "Bearer test-agent-token-that-is-long-enough",
  };
  try {
    for (const path of [
      "/v1/sessions?limit=0",
      "/v1/sessions?limit=101",
      `/v1/sessions?q=${"x".repeat(201)}`,
      "/v1/sessions?cursor=../../secret",
      "/v1/runs?sessionId=../../secret",
    ]) {
      const response = await fetch(`${app.baseUrl}${path}`, { headers });
      assert.equal(response.status, 400);
      assert.deepEqual(await response.json(), {
        error: { code: "INVALID_REQUEST", message: "Invalid history request" },
      });
    }
    assert.equal(called, false);

    const missingSession = await fetch(
      `${app.baseUrl}/v1/sessions/missing-session`,
      { headers },
    );
    assert.equal(missingSession.status, 404);
    assert.deepEqual(await missingSession.json(), {
      error: { code: "NOT_FOUND", message: "Session not found" },
    });

    const missingAudit = await fetch(
      `${app.baseUrl}/v1/runs/33333333-3333-4333-8333-333333333333/audit`,
      { headers },
    );
    assert.equal(missingAudit.status, 404);
    assert.deepEqual(await missingAudit.json(), {
      error: { code: "NOT_FOUND", message: "Run audit not found" },
    });
  } finally {
    await app.close();
  }
});

test("rejects unauthenticated history requests", async () => {
  let called = false;
  const engine = {
    async *run() {},
    async cancel() {
      return false;
    },
    async listSessions() {
      called = true;
    },
    async getSession() {
      called = true;
    },
    async listRunAudits() {
      called = true;
    },
    async getRunAudit() {
      called = true;
    },
  };
  const app = await listen(engine);
  try {
    for (const path of [
      "/v1/sessions",
      "/v1/sessions/session-1",
      "/v1/runs",
      `/v1/runs/${validRun.runId}/audit`,
    ]) {
      const response = await fetch(`${app.baseUrl}${path}`);
      assert.equal(response.status, 401);
    }
    assert.equal(called, false);
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
