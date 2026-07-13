import { homedir } from "node:os";
import { join } from "node:path";

const REASONING_LEVELS = new Set([
  "none",
  "minimal",
  "low",
  "medium",
  "high",
  "xhigh",
  "max",
]);

function required(name) {
  const value = process.env[name]?.trim();
  if (!value) throw new Error(`Missing required configuration: ${name}`);
  return value;
}

function integer(name, fallback, { min = 1, max = Number.MAX_SAFE_INTEGER } = {}) {
  const raw = process.env[name]?.trim();
  const value = raw ? Number(raw) : fallback;
  if (!Number.isInteger(value) || value < min || value > max) {
    throw new Error(`Invalid integer configuration: ${name}`);
  }
  return value;
}

export function loadConfig() {
  const reasoningEffort =
    process.env.TELEFIRE_AI_REASONING_EFFORT?.trim().toLowerCase() || "none";
  if (!REASONING_LEVELS.has(reasoningEffort)) {
    throw new Error("Invalid configuration: TELEFIRE_AI_REASONING_EFFORT");
  }
  const dataDir =
    process.env.TELEFIRE_PI_DATA_DIR?.trim() || join(homedir(), ".telefire-pi");
  return {
    host: process.env.TELEFIRE_PI_HOST?.trim() || "0.0.0.0",
    port: integer("TELEFIRE_PI_PORT", 8790, { max: 65_535 }),
    serviceToken: required("TELEFIRE_PI_TOKEN"),
    engine: {
      baseUrl: required("TELEFIRE_AI_BASE_URL"),
      apiKey: required("TELEFIRE_AI_API_KEY"),
      model: required("TELEFIRE_AI_CHAT_MODEL"),
      reasoningEffort,
      maxOutputTokens: integer("TELEFIRE_AI_MAX_OUTPUT_TOKENS", 4_000, {
        max: 100_000,
      }),
      contextWindow: integer("TELEFIRE_AI_CONTEXT_WINDOW", 128_000, {
        min: 4_096,
      }),
      requestTimeoutMs:
        integer("TELEFIRE_AI_REQUEST_TIMEOUT", 90, { max: 3_600 }) * 1_000,
      memoryUrl:
        process.env.TELEFIRE_HINDSIGHT_URL?.trim().replace(/\/$/, "") || null,
      workspaceDir: join(dataDir, "workspace"),
      sessionDir: join(dataDir, "sessions"),
      agentDir: join(dataDir, "agent"),
    },
  };
}
