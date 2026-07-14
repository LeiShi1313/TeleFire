import assert from "node:assert/strict";
import test from "node:test";

import { loadConfig } from "../src/config.mjs";

test("loads the standalone agent configuration without Telefire names", () => {
  Object.assign(process.env, {
    AI_BASE_URL: "http://provider.internal/v1",
    AI_API_KEY: "test-key",
    AI_CHAT_MODEL: "test-model",
    AI_REASONING_EFFORT: "low",
    PI_AGENT_TOKEN: "test-agent-token",
    PI_DATA_DIR: "/tmp/pi-agent-test",
    MEMORY_API_URL: "http://memory.internal:8888/",
  });

  const config = loadConfig();

  assert.equal(config.serviceToken, "test-agent-token");
  assert.equal(config.engine.baseUrl, "http://provider.internal/v1");
  assert.equal(config.engine.apiKey, "test-key");
  assert.equal(config.engine.model, "test-model");
  assert.equal(config.engine.reasoningEffort, "low");
  assert.equal(config.engine.memoryUrl, "http://memory.internal:8888");
  assert.equal(config.engine.workspaceDir, "/tmp/pi-agent-test/workspace");
});
