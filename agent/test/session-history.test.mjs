import assert from "node:assert/strict";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

import { SessionManager } from "@earendil-works/pi-coding-agent";

import { SessionHistory } from "../src/session-history.mjs";

const usage = {
  input: 12,
  output: 8,
  cacheRead: 0,
  cacheWrite: 0,
  totalTokens: 20,
  cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, total: 0 },
};

async function fixture() {
  const root = await mkdtemp(join(tmpdir(), "telefire-session-history-"));
  const workspaceDir = join(root, "workspace");
  const sessionDir = join(root, "sessions");
  const manager = SessionManager.create(workspaceDir, sessionDir, {
    id: "11111111-1111-4111-8111-111111111111",
  });
  const userId = manager.appendMessage({
    role: "user",
    content: "<current_request>\nInspect deployment history\n</current_request>",
    timestamp: 1,
  });
  manager.appendMessage({
    role: "assistant",
    content: [
      { type: "thinking", thinking: "I should inspect the records." },
      {
        type: "toolCall",
        id: "call-1",
        name: "memory_reflect",
        arguments: {
          question: "Who owns deployment?",
          authorization: "Bearer must-not-leak",
        },
      },
    ],
    api: "openai-completions",
    provider: "openai-compatible",
    model: "test-model",
    usage,
    stopReason: "toolUse",
    timestamp: 2,
  });
  manager.appendMessage({
    role: "toolResult",
    toolCallId: "call-1",
    toolName: "memory_reflect",
    content: [{ type: "text", text: "Alice owns deployment." }],
    details: { memoryIds: ["memory-1"] },
    isError: false,
    timestamp: 3,
  });
  manager.appendMessage({
    role: "assistant",
    content: [{ type: "text", text: "Alice owns deployment." }],
    api: "openai-completions",
    provider: "openai-compatible",
    model: "test-model",
    usage,
    stopReason: "stop",
    timestamp: 4,
  });
  manager.branch(userId);
  const branchId = manager.appendMessage({
    role: "user",
    content: "Try the other branch",
    timestamp: 5,
  });
  manager.appendSessionInfo("Deployment investigation");

  return {
    history: new SessionHistory({ workspaceDir, sessionDir }),
    root,
    sessionDir,
    sessionId: manager.getSessionId(),
    branchId,
    close: () => rm(root, { recursive: true, force: true }),
  };
}

test("lists persisted sessions with stable cursor pagination and search", async () => {
  const app = await fixture();
  try {
    const page = await app.history.list({ limit: 1, query: "deployment" });

    assert.equal(page.total, 1);
    assert.equal(page.nextCursor, null);
    assert.equal(page.items[0].id, app.sessionId);
    assert.equal(page.items[0].name, "Deployment investigation");
    assert.equal(page.items[0].messageCount, 5);
    assert.match(page.items[0].firstMessage, /Inspect deployment history/);

    const empty = await app.history.list({ limit: 20, query: "unrelated" });
    assert.deepEqual(empty, { items: [], total: 0, nextCursor: null });
  } finally {
    await app.close();
  }
});

test("recovers valid sessions created under a different workspace", async () => {
  const app = await fixture();
  try {
    const legacy = SessionManager.create(
      join(app.root, "legacy-workspace"),
      app.sessionDir,
      { id: "22222222-2222-4222-8222-222222222222" },
    );
    legacy.appendMessage({
      role: "user",
      content: "A message from the old workspace",
      timestamp: 10,
    });
    legacy.appendMessage({
      role: "assistant",
      content: [{ type: "text", text: "A legacy answer" }],
      api: "openai-completions",
      provider: "openai-compatible",
      model: "test-model",
      usage,
      stopReason: "stop",
      timestamp: 11,
    });

    const page = await app.history.list({ limit: 20 });
    assert.equal(page.total, 2);
    assert(page.items.some((item) => item.id === legacy.getSessionId()));
    assert.equal((await app.history.get(legacy.getSessionId())).id, legacy.getSessionId());
  } finally {
    await app.close();
  }
});

test("returns the complete branchable session tree with bounded redaction", async () => {
  const app = await fixture();
  try {
    const detail = await app.history.get(app.sessionId);

    assert.equal(detail.id, app.sessionId);
    assert.equal(detail.name, "Deployment investigation");
    assert.equal(detail.leafId, detail.entries.at(-1).id);
    assert(detail.entries.some((entry) => entry.id === app.branchId));
    assert(detail.entries.some((entry) => entry.parentId !== detail.entries.at(-1).parentId));

    const toolEntry = detail.entries.find(
      (entry) =>
        Array.isArray(entry.message?.content) &&
        entry.message.content.some((part) => part.type === "toolCall"),
    );
    const toolCall = toolEntry.message.content.find(
      (part) => part.type === "toolCall",
    );
    assert.equal(toolCall.arguments.question, "Who owns deployment?");
    assert.equal(toolCall.arguments.authorization, "[REDACTED]");
    assert.match(JSON.stringify(detail), /Alice owns deployment/);
    assert.doesNotMatch(JSON.stringify(detail), /must-not-leak/);
  } finally {
    await app.close();
  }
});

test("returns null for unknown or malformed session identities", async () => {
  const app = await fixture();
  try {
    assert.equal(await app.history.get("missing-session"), null);
    assert.equal(await app.history.get("../../sessions"), null);
  } finally {
    await app.close();
  }
});
