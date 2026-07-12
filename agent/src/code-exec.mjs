import {
  getQuickJS,
  shouldInterruptAfterDeadline,
} from "quickjs-emscripten";

const DEFAULT_LIMITS = Object.freeze({
  maxCodeChars: 16_000,
  maxOutputChars: 16_000,
  memoryLimitBytes: 16 * 1024 * 1024,
  stackLimitBytes: 512 * 1024,
  timeoutMs: 2_000,
});

function truncate(text, limit) {
  return text.length <= limit ? text : `${text.slice(0, Math.max(0, limit - 3))}...`;
}

function renderValue(value) {
  if (value === undefined) return "";
  if (typeof value === "string") return value;
  try {
    return JSON.stringify(value);
  } catch {
    return String(value);
  }
}

export async function executeJavaScript(source, options = {}) {
  const limits = { ...DEFAULT_LIMITS, ...options };
  if (typeof source !== "string" || !source.trim()) {
    throw new Error("JavaScript code is required");
  }
  if (source.length > limits.maxCodeChars) {
    throw new Error("JavaScript code limit exceeded");
  }

  const QuickJS = await getQuickJS();
  const runtime = QuickJS.newRuntime();
  runtime.setMemoryLimit(limits.memoryLimitBytes);
  runtime.setMaxStackSize(limits.stackLimitBytes);
  runtime.setInterruptHandler(
    shouldInterruptAfterDeadline(Date.now() + limits.timeoutMs),
  );
  const vm = runtime.newContext();
  const logs = [];

  try {
    const consoleHandle = vm.newObject();
    const logHandle = vm.newFunction("log", (...args) => {
      logs.push(args.map((arg) => renderValue(vm.dump(arg))).join(" "));
      return vm.undefined;
    });
    vm.setProp(consoleHandle, "log", logHandle);
    vm.setProp(vm.global, "console", consoleHandle);
    logHandle.dispose();
    consoleHandle.dispose();

    const evaluation = vm.evalCode(source, "code_exec.js");
    if (evaluation.error) {
      const error = renderValue(vm.dump(evaluation.error));
      evaluation.error.dispose();
      if (/interrupted/i.test(error)) {
        throw new Error("JavaScript execution time limit exceeded");
      }
      if (/memory|stack/i.test(error)) {
        throw new Error("JavaScript execution resource limit exceeded");
      }
      throw new Error(`JavaScript execution failed: ${error}`);
    }

    const value = renderValue(vm.dump(evaluation.value));
    evaluation.value.dispose();
    const output = [...logs, value].filter(Boolean).join("\n") || "undefined";
    return truncate(output, limits.maxOutputChars);
  } finally {
    vm.dispose();
    runtime.dispose();
  }
}

export { DEFAULT_LIMITS as CODE_EXEC_LIMITS };
