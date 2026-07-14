import assert from "node:assert/strict";
import test from "node:test";

import { executeJavaScript } from "../src/code-exec.mjs";

test("executes calculations and returns logs plus the final value", async () => {
  const result = await executeJavaScript(
    'console.log("working"); [1, 2, 3, 4].reduce((sum, value) => sum + value, 0)',
  );

  assert.equal(result, "working\n10");
});

test("does not expose Node or host APIs", async () => {
  const result = await executeJavaScript(
    "({ process: typeof process, require: typeof require, fetch: typeof fetch })",
  );

  assert.equal(
    result,
    '{"process":"undefined","require":"undefined","fetch":"undefined"}',
  );
});

test("interrupts non-terminating code", async () => {
  await assert.rejects(
    executeJavaScript("while (true) {}", { timeoutMs: 20 }),
    /time limit/i,
  );
});

test("rejects oversized source and truncates output", async () => {
  await assert.rejects(
    executeJavaScript("x".repeat(101), { maxCodeChars: 100 }),
    /code limit/i,
  );

  const result = await executeJavaScript('"x".repeat(100)', {
    maxOutputChars: 20,
  });
  assert.equal(result, `${"x".repeat(17)}...`);
});
