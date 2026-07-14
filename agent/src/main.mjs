import { mkdir, writeFile } from "node:fs/promises";
import { createRequire } from "node:module";
import { homedir } from "node:os";
import { dirname, join } from "node:path";

import { loadConfig } from "./config.mjs";
import { createAgentServer } from "./http-service.mjs";
import { PiEngine } from "./pi-engine.mjs";

const require = createRequire(import.meta.url);

function logger(level, message, fields = {}) {
  const safe = { time: new Date().toISOString(), level, message, ...fields };
  process.stderr.write(`${JSON.stringify(safe)}\n`);
}

async function writeWebConfig() {
  const path = join(homedir(), ".pi", "web-search.json");
  await mkdir(dirname(path), { recursive: true, mode: 0o700 });
  await writeFile(
    path,
    `${JSON.stringify(
      {
        provider: "exa",
        workflow: "none",
        allowBrowserCookies: false,
        webSearch: { enabled: true },
        githubClone: { enabled: false },
        youtube: { enabled: false },
      },
      null,
      2,
    )}\n`,
    { mode: 0o600 },
  );
}

async function main() {
  const config = loadConfig();
  await writeWebConfig();
  const packagePath = require.resolve("pi-web-access/package.json");
  const engine = new PiEngine({
    ...config.engine,
    webExtensionPath: join(dirname(packagePath), "index.ts"),
  });
  await engine.initialize();

  const server = createAgentServer({
    engine,
    token: config.serviceToken,
    logger: {
      info: (message, fields) => logger("info", message, fields),
      error: (message, fields) => logger("error", message, fields),
    },
  });
  await new Promise((resolve, reject) => {
    server.once("error", reject);
    server.listen(config.port, config.host, resolve);
  });
  logger("info", "Pi agent service started", {
    host: config.host,
    port: config.port,
  });

  let stopping = false;
  const stop = async () => {
    if (stopping) return;
    stopping = true;
    await engine.shutdown();
    await new Promise((resolve) => server.close(resolve));
  };
  process.once("SIGTERM", () => void stop());
  process.once("SIGINT", () => void stop());
}

main().catch((error) => {
  logger("error", "Pi agent service failed to start", {
    error: error instanceof Error ? error.message : String(error),
  });
  process.exitCode = 1;
});
