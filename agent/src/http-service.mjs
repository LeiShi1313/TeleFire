import { createHash, timingSafeEqual } from "node:crypto";
import { createServer } from "node:http";

const MAX_BODY_BYTES = 64 * 1024;
const MAX_ATTACHMENT_BODY_BYTES = 3 * 1024 * 1024;
const MAX_ATTACHMENT_BYTES = 2 * 1024 * 1024;
const MAX_ATTACHMENT_TEXT_CHARS = 50_000;
const MAX_MEMORY_ANCHORS = 64;
const MAX_BANK_GRANTS = 64;
const MAX_PARTICIPANTS = 16;
const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const IDENTIFIER_RE = /^[A-Za-z0-9_-]{1,128}$/;
const BANK_ID_RE = /^[A-Za-z0-9][A-Za-z0-9:_.%-]{0,255}$/;
const MIME_RE = /^[a-z0-9][a-z0-9.+-]{0,63}\/[a-z0-9][a-z0-9.+-]{0,127}$/;
const IMAGE_MIME_TYPES = new Set(["image/jpeg", "image/png", "image/webp"]);

function json(response, status, payload) {
  const body = JSON.stringify(payload);
  response.writeHead(status, {
    "content-type": "application/json; charset=utf-8",
    "content-length": Buffer.byteLength(body),
    "cache-control": "no-store",
  });
  response.end(body);
}

async function readJson(request, maxBytes = MAX_BODY_BYTES) {
  if (!request.headers["content-type"]?.toLowerCase().startsWith("application/json")) {
    throw new Error("invalid content type");
  }
  const chunks = [];
  let size = 0;
  for await (const chunk of request) {
    size += chunk.length;
    if (size > maxBytes) throw new Error("request too large");
    chunks.push(chunk);
  }
  return JSON.parse(Buffer.concat(chunks).toString("utf8"));
}

function isBoundedString(value, min, max) {
  return typeof value === "string" && value.length >= min && value.length <= max;
}

function hasOnlyKeys(value, allowed) {
  return Object.keys(value).every((key) => allowed.has(key));
}

function isBankId(value) {
  return typeof value === "string" && BANK_ID_RE.test(value);
}

function boundedBankIds(value) {
  if (!Array.isArray(value) || value.length > MAX_BANK_GRANTS) return null;
  const unique = new Set(value);
  if (
    unique.size !== value.length ||
    value.some((item) => !isBankId(item))
  ) {
    return null;
  }
  return [...value];
}

function isActorId(value) {
  return isBankId(value) && value.includes(":user:");
}

function listOptions(url, kind) {
  const allowed =
    kind === "sessions"
      ? new Set(["limit", "cursor", "q"])
      : new Set(["limit", "cursor", "sessionId"]);
  for (const key of url.searchParams.keys()) {
    if (!allowed.has(key) || url.searchParams.getAll(key).length !== 1) {
      return null;
    }
  }
  const rawLimit = url.searchParams.get("limit");
  const limit = rawLimit === null ? 50 : Number(rawLimit);
  const cursor = url.searchParams.get("cursor");
  if (
    !Number.isInteger(limit) ||
    limit < 1 ||
    limit > 100 ||
    (cursor !== null && !IDENTIFIER_RE.test(cursor))
  ) {
    return null;
  }
  if (kind === "sessions") {
    const query = url.searchParams.get("q") ?? "";
    if (query.length > 200) return null;
    return { limit, cursor, query };
  }
  const sessionId = url.searchParams.get("sessionId");
  if (sessionId !== null && !IDENTIFIER_RE.test(sessionId)) return null;
  return { limit, cursor, sessionId };
}

export function validateRunRequest(value) {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  const sessionId = value.sessionId;
  const parentEntryId = value.parentEntryId;
  const includeMemorySnapshot = value.includeMemorySnapshot;
  const isRoot = sessionId === null && parentEntryId === null;
  const isContinuation =
    typeof sessionId === "string" &&
    IDENTIFIER_RE.test(sessionId) &&
    typeof parentEntryId === "string" &&
    IDENTIFIER_RE.test(parentEntryId);
  if (
    !UUID_RE.test(value.runId ?? "") ||
    (!isRoot && !isContinuation) ||
    !isBoundedString(value.prompt, 1, 16_000) ||
    !isBoundedString(value.systemPrompt, 1, 32_000) ||
    !new Set(["owner", "delegated", "none"]).has(value.toolPolicy) ||
    !(
      includeMemorySnapshot === undefined ||
      typeof includeMemorySnapshot === "boolean"
    ) ||
    !Array.isArray(value.context) ||
    value.context.length > 4
  ) {
    return null;
  }
  const context = [];
  for (const item of value.context) {
    if (
      !item ||
      typeof item !== "object" ||
      item.kind !== "reference" ||
      !isBoundedString(item.text, 1, 16_000)
    ) {
      return null;
    }
    context.push({ kind: item.kind, text: item.text });
  }
  let memory;
  if (value.memory !== undefined) {
    const supplied = value.memory;
    if (
      !supplied ||
      typeof supplied !== "object" ||
      Array.isArray(supplied) ||
      !hasOnlyKeys(
        supplied,
        new Set([
          "primaryBankId",
          "requester",
          "grantedBankIds",
          "participants",
          "query",
          "anchors",
        ]),
      ) ||
      !isBankId(supplied.primaryBankId) ||
      !(
        supplied.query === undefined ||
        supplied.query === null ||
        isBoundedString(supplied.query, 1, 8_000)
      ) ||
      !Array.isArray(supplied.anchors) ||
      supplied.anchors.length > MAX_MEMORY_ANCHORS ||
      !Array.isArray(supplied.participants) ||
      supplied.participants.length > MAX_PARTICIPANTS
    ) {
      return null;
    }
    const requester = supplied.requester;
    const grantedBankIds = boundedBankIds(supplied.grantedBankIds);
    if (
      !requester ||
      typeof requester !== "object" ||
      Array.isArray(requester) ||
      !hasOnlyKeys(requester, new Set(["id", "label", "owner"])) ||
      !isActorId(requester.id) ||
      !(
        requester.label === null ||
        requester.label === undefined ||
        isBoundedString(requester.label, 1, 256)
      ) ||
      typeof requester.owner !== "boolean" ||
      grantedBankIds === null ||
      (requester.owner && grantedBankIds.length > 0)
    ) {
      return null;
    }
    const anchors = [];
    const seen = new Set();
    for (const anchor of supplied.anchors) {
      if (
        !anchor ||
        typeof anchor !== "object" ||
        Array.isArray(anchor) ||
        !hasOnlyKeys(anchor, new Set(["id", "label"])) ||
        !isBoundedString(anchor.id, 1, 256) ||
        !(
          anchor.label === null ||
          anchor.label === undefined ||
          isBoundedString(anchor.label, 1, 256)
        ) ||
        seen.has(anchor.id)
      ) {
        return null;
      }
      seen.add(anchor.id);
      anchors.push({
        id: anchor.id,
        label: anchor.label ?? null,
      });
    }
    const participants = [];
    const participantIds = new Set();
    for (const participant of supplied.participants) {
      const bankIds = boundedBankIds(participant?.bankIds);
      if (
        !participant ||
        typeof participant !== "object" ||
        Array.isArray(participant) ||
        !hasOnlyKeys(
          participant,
          new Set(["id", "label", "allowed", "bankIds"]),
        ) ||
        !isActorId(participant.id) ||
        participant.id === requester.id ||
        participantIds.has(participant.id) ||
        !(
          participant.label === null ||
          participant.label === undefined ||
          isBoundedString(participant.label, 1, 256)
        ) ||
        typeof participant.allowed !== "boolean" ||
        bankIds === null ||
        (!participant.allowed && bankIds.length > 0)
      ) {
        return null;
      }
      participantIds.add(participant.id);
      participants.push({
        id: participant.id,
        label: participant.label ?? null,
        allowed: participant.allowed,
        bankIds,
      });
    }
    memory = {
      primaryBankId: supplied.primaryBankId,
      requester: {
        id: requester.id,
        label: requester.label ?? null,
        owner: requester.owner,
      },
      grantedBankIds,
      participants,
      ...(supplied.query ? { query: supplied.query } : {}),
      anchors,
    };
  }
  return {
    runId: value.runId,
    sessionId,
    parentEntryId,
    prompt: value.prompt,
    context,
    systemPrompt: value.systemPrompt,
    toolPolicy: value.toolPolicy,
    ...(includeMemorySnapshot ? { includeMemorySnapshot: true } : {}),
    ...(memory ? { memory } : {}),
  };
}

function decodeBase64(value) {
  if (
    typeof value !== "string" ||
    value.length < 4 ||
    value.length > Math.ceil(MAX_ATTACHMENT_BYTES / 3) * 4 + 4 ||
    !/^[A-Za-z0-9+/]+={0,2}$/.test(value)
  ) {
    return null;
  }
  const decoded = Buffer.from(value, "base64");
  const expected = value.replace(/=+$/, "");
  const actual = decoded.toString("base64").replace(/=+$/, "");
  if (
    decoded.length === 0 ||
    decoded.length > MAX_ATTACHMENT_BYTES ||
    actual !== expected
  ) {
    return null;
  }
  return decoded;
}

export function validateAttachmentRequest(value) {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  const mimeType = value.mimeType;
  const filename = value.filename;
  if (
    typeof mimeType !== "string" ||
    !MIME_RE.test(mimeType) ||
    !(
      filename === null ||
      filename === undefined ||
      isBoundedString(filename, 1, 200)
    )
  ) {
    return null;
  }
  if (value.kind === "image" && IMAGE_MIME_TYPES.has(mimeType)) {
    const data = decodeBase64(value.data);
    if (!data || value.text !== undefined) return null;
    return { kind: "image", mimeType, filename: filename ?? null, data };
  }
  if (
    value.kind === "text" &&
    isBoundedString(value.text, 1, MAX_ATTACHMENT_TEXT_CHARS) &&
    value.data === undefined
  ) {
    return {
      kind: "text",
      mimeType,
      filename: filename ?? null,
      text: value.text,
    };
  }
  return null;
}

function writeNdjson(response, event) {
  return response.write(`${JSON.stringify(event)}\n`);
}

function isAuthorized(request, tokenDigest) {
  const actual = request.headers.authorization ?? "";
  const actualDigest = createHash("sha256").update(actual).digest();
  return timingSafeEqual(actualDigest, tokenDigest);
}

export function createAgentServer({ engine, token, logger = console }) {
  if (typeof token !== "string" || token.length < 24) {
    throw new Error("Agent service token must contain at least 24 characters");
  }
  const tokenDigest = createHash("sha256").update(`Bearer ${token}`).digest();
  return createServer(async (request, response) => {
    response.setHeader("x-content-type-options", "nosniff");
    response.setHeader("cache-control", "no-store");
    const url = new URL(request.url ?? "/", "http://agent.invalid");

    if (request.method === "GET" && url.pathname === "/health") {
      json(response, 200, { status: "ok" });
      return;
    }

    if (!isAuthorized(request, tokenDigest)) {
      json(response, 401, {
        error: { code: "UNAUTHORIZED", message: "Unauthorized" },
      });
      return;
    }

    if (request.method === "GET" && url.pathname === "/v1/sessions") {
      const options = listOptions(url, "sessions");
      if (!options) {
        json(response, 400, {
          error: { code: "INVALID_REQUEST", message: "Invalid history request" },
        });
        return;
      }
      try {
        json(response, 200, await engine.listSessions(options));
      } catch {
        json(response, 500, {
          error: {
            code: "HISTORY_UNAVAILABLE",
            message: "Session history unavailable",
          },
        });
      }
      return;
    }

    const sessionMatch = url.pathname.match(
      /^\/v1\/sessions\/([A-Za-z0-9_-]{1,128})$/,
    );
    if (request.method === "GET" && sessionMatch) {
      try {
        const session = await engine.getSession(sessionMatch[1]);
        if (!session) {
          json(response, 404, {
            error: { code: "NOT_FOUND", message: "Session not found" },
          });
        } else {
          json(response, 200, session);
        }
      } catch {
        json(response, 500, {
          error: {
            code: "HISTORY_UNAVAILABLE",
            message: "Session history unavailable",
          },
        });
      }
      return;
    }

    if (request.method === "GET" && url.pathname === "/v1/runs") {
      const options = listOptions(url, "runs");
      if (!options) {
        json(response, 400, {
          error: { code: "INVALID_REQUEST", message: "Invalid history request" },
        });
        return;
      }
      try {
        json(response, 200, await engine.listRunAudits(options));
      } catch {
        json(response, 500, {
          error: {
            code: "HISTORY_UNAVAILABLE",
            message: "Run history unavailable",
          },
        });
      }
      return;
    }

    const auditMatch = url.pathname.match(
      /^\/v1\/runs\/([0-9a-f-]+)\/audit$/i,
    );
    if (request.method === "GET" && auditMatch) {
      if (!UUID_RE.test(auditMatch[1])) {
        json(response, 400, {
          error: { code: "INVALID_REQUEST", message: "Invalid history request" },
        });
        return;
      }
      try {
        const audit = await engine.getRunAudit(auditMatch[1]);
        if (!audit) {
          json(response, 404, {
            error: { code: "NOT_FOUND", message: "Run audit not found" },
          });
        } else {
          json(response, 200, audit);
        }
      } catch {
        json(response, 500, {
          error: {
            code: "HISTORY_UNAVAILABLE",
            message: "Run history unavailable",
          },
        });
      }
      return;
    }

    if (request.method === "POST" && url.pathname === "/v1/attachments/describe") {
      let attachment;
      try {
        attachment = validateAttachmentRequest(
          await readJson(request, MAX_ATTACHMENT_BODY_BYTES),
        );
      } catch {
        attachment = null;
      }
      if (!attachment) {
        json(response, 400, {
          error: { code: "INVALID_REQUEST", message: "Invalid attachment request" },
        });
        return;
      }
      try {
        const description = await engine.describeAttachment(attachment);
        if (!isBoundedString(description, 1, 4_000)) {
          throw new Error("Invalid attachment description");
        }
        json(response, 200, { description });
      } catch (error) {
        logger.error("Attachment analysis failed", {
          errorType: error instanceof Error ? error.name : "UnknownError",
        });
        json(response, 502, {
          error: { code: "ANALYSIS_FAILED", message: "Attachment analysis failed" },
        });
      }
      return;
    }

    const cancelMatch = url.pathname.match(
      /^\/v1\/runs\/([0-9a-f-]+)\/cancel$/i,
    );
    if (request.method === "POST" && cancelMatch) {
      const runId = cancelMatch[1];
      if (!UUID_RE.test(runId)) {
        json(response, 400, {
          error: { code: "INVALID_REQUEST", message: "Invalid run id" },
        });
        return;
      }
      json(response, 200, { cancelled: await engine.cancel(runId) });
      return;
    }

    if (request.method !== "POST" || url.pathname !== "/v1/runs") {
      json(response, 404, {
        error: { code: "NOT_FOUND", message: "Not found" },
      });
      return;
    }

    let run;
    try {
      run = validateRunRequest(await readJson(request));
    } catch {
      run = null;
    }
    if (!run) {
      json(response, 400, {
        error: { code: "INVALID_REQUEST", message: "Invalid run request" },
      });
      return;
    }

    response.writeHead(200, {
      "content-type": "application/x-ndjson; charset=utf-8",
      "transfer-encoding": "chunked",
    });
    let completed = false;
    response.on("close", () => {
      if (!completed) void engine.cancel(run.runId);
    });
    try {
      for await (const event of engine.run(run)) {
        if (response.destroyed) break;
        writeNdjson(response, event);
      }
      completed = true;
    } catch (error) {
      logger.error("Agent run failed", {
        runId: run.runId,
        errorType: error instanceof Error ? error.name : "UnknownError",
      });
      if (!response.destroyed) {
        writeNdjson(response, {
          type: "run_failed",
          code: "AGENT_ERROR",
          message: "Agent run failed",
        });
      }
    } finally {
      if (!response.destroyed) response.end();
    }
  });
}
