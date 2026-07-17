import { createHash } from "node:crypto";

export const KNOWLEDGE_DIRECTORY_BANK_ID = "system:knowledge-directory";
export const KNOWLEDGE_DIRECTORY_SCHEMA = "telefire.knowledge-directory.v1";

const BANK_ID_RE = /^[A-Za-z0-9][A-Za-z0-9:_.%-]{0,255}$/;
const TOKEN_RE = /^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$/;
const MAX_DIRECTORY_RESULTS = 1_000;
const MAX_DIRECTORY_REFERENCES = 32;

export function bankReferenceTag(bankId) {
  if (typeof bankId !== "string" || !BANK_ID_RE.test(bankId)) {
    throw new Error("Invalid knowledge source bank");
  }
  return `telefire:bank-ref:${createHash("sha256").update(bankId).digest("hex")}`;
}

function provenance(item, sourceFacts) {
  if (item.source_fact_ids == null) {
    if (item.metadata == null) return null;
    return { metadata: item.metadata, tags: item.tags };
  }
  if (
    !Array.isArray(item.source_fact_ids) ||
    item.source_fact_ids.length < 1 ||
    item.source_fact_ids.length > 50 ||
    item.source_fact_ids.some((id) => typeof id !== "string") ||
    new Set(item.source_fact_ids).size !== item.source_fact_ids.length
  ) {
    throw new Error("Malformed knowledge directory provenance");
  }
  const candidates = item.source_fact_ids.map((id) => sourceFacts[id]);
  if (candidates.some((candidate) => candidate === undefined)) return null;
  if (
    candidates.some(
      (candidate) =>
        !candidate || typeof candidate !== "object" || Array.isArray(candidate),
    )
  ) {
    throw new Error("Malformed knowledge directory provenance");
  }
  const first = candidates[0];
  const serializedMetadata = JSON.stringify(first.metadata);
  const serializedTags = JSON.stringify(first.tags);
  if (
    candidates.some(
      (candidate) =>
        JSON.stringify(candidate.metadata) !== serializedMetadata ||
        JSON.stringify(candidate.tags) !== serializedTags,
    ) ||
    JSON.stringify(item.tags) !== serializedTags
  ) {
    throw new Error("Conflicting knowledge directory provenance");
  }
  return { metadata: first.metadata, tags: first.tags };
}

function validatedReference(item, sourceFacts, allowedBankIds) {
  if (
    !item ||
    typeof item !== "object" ||
    Array.isArray(item) ||
    typeof item.id !== "string" ||
    item.id.length < 1 ||
    item.id.length > 128 ||
    typeof item.text !== "string" ||
    item.text.length < 1 ||
    item.text.length > 16_000
  ) {
    throw new Error("Malformed knowledge directory reference");
  }
  const trusted = provenance(item, sourceFacts);
  if (trusted === null) return null;
  const metadata = trusted?.metadata;
  const tags = trusted?.tags;
  const bankId = metadata?.bank_id;
  if (
    !metadata ||
    typeof metadata !== "object" ||
    Array.isArray(metadata) ||
    typeof bankId !== "string" ||
    !BANK_ID_RE.test(bankId)
  ) {
    throw new Error("Malformed knowledge directory reference");
  }
  const expectedTag = bankReferenceTag(bankId);
  if (
    metadata.client !== "telefire" ||
    metadata.source !== "knowledge-directory" ||
    metadata.schema !== KNOWLEDGE_DIRECTORY_SCHEMA ||
    metadata.bank_ref !== expectedTag ||
    !Array.isArray(tags) ||
    tags.length !== 1 ||
    tags[0] !== expectedTag ||
    typeof metadata.source_name !== "string" ||
    metadata.source_name.trim().length < 1 ||
    metadata.source_name.length > 256 ||
    typeof metadata.source_platform !== "string" ||
    !TOKEN_RE.test(metadata.source_platform) ||
    typeof metadata.source_kind !== "string" ||
    !TOKEN_RE.test(metadata.source_kind) ||
    (allowedBankIds !== null && !allowedBankIds.has(bankId))
  ) {
    throw new Error("Malformed knowledge directory reference");
  }
  return {
    bankId,
    displayName: metadata.source_name.trim(),
    platform: metadata.source_platform,
    sourceKind: metadata.source_kind,
    evidence: {
      memoryId: item.id,
      text: item.text,
      type: typeof item.type === "string" ? item.type : null,
      documentId: typeof item.document_id === "string" ? item.document_id : null,
    },
  };
}

export function parseDirectoryRecall(payload, allowedBankIds = null) {
  const results = payload?.results;
  const sourceFacts = payload?.source_facts ?? {};
  if (
    !Array.isArray(results) ||
    results.length > MAX_DIRECTORY_RESULTS ||
    !sourceFacts ||
    typeof sourceFacts !== "object" ||
    Array.isArray(sourceFacts)
  ) {
    throw new Error("Malformed knowledge directory response");
  }
  const allowed =
    allowedBankIds === null ? null : new Set(allowedBankIds);
  const grouped = new Map();
  for (const item of results) {
    const reference = validatedReference(item, sourceFacts, allowed);
    if (reference === null) continue;
    const previous = grouped.get(reference.bankId);
    if (!previous) {
      if (grouped.size >= MAX_DIRECTORY_REFERENCES) break;
      grouped.set(reference.bankId, {
        bankId: reference.bankId,
        displayName: reference.displayName,
        platform: reference.platform,
        sourceKind: reference.sourceKind,
        evidence: [reference.evidence],
      });
      continue;
    }
    if (
      previous.displayName !== reference.displayName ||
      previous.platform !== reference.platform ||
      previous.sourceKind !== reference.sourceKind
    ) {
      throw new Error("Conflicting knowledge directory reference");
    }
    previous.evidence.push(reference.evidence);
  }
  return [...grouped.values()];
}
