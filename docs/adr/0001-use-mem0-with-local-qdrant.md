---
status: superseded by ADR-0002
---

# Use Mem0 with local Qdrant for semantic memory

Telefire will use Mem0 OSS `AsyncMemory` for memory extraction and lifecycle policy, with local Qdrant as the semantic store. Mem0's SQLite history database is acceptable as internal audit state but is not the semantic memory backend; this avoids building a complete memory engine over Zvec while retaining local data, OpenAI-compatible providers, scoped retrieval, and explicit update and deletion operations. TencentDB Agent Memory was rejected for now because its reviewed gateway does not apply per-request user identity or expose the required lifecycle APIs, while Zvec was rejected as the initial choice because it provides retrieval storage rather than memory policy. See [the backend research](../research/ai-memory-backends.md).

This decision was reopened on 2026-07-11 after the memory system was broadened into a portable context-augmentation module with its own episode extraction, subject and scope model, profile synthesis, and context packing. Those responsibilities reduce the leverage gained from Mem0's heavier dependency stack.
