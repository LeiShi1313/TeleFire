# AI Memory Backend Research

Date: 2026-07-10

## Telefire Requirements

- Embed in, or remain simple to operate beside, one long-running async Python 3.14 userbot.
- Use OpenAI-compatible chat and embedding endpoints.
- Isolate memory by Telegram account, chat, user, and memory scope.
- Extract useful memories after successful AI turns and retrieve relevant memories before later turns.
- Support explicit owner-driven correction, deletion, and provenance back to Telegram messages.
- Maintain a human-readable global user profile plus chat-scoped memories.
- Keep data self-hosted and avoid a mandatory cloud service.

## Important Distinction

A vector database is a retrieval index, not a complete memory system. It can store embeddings and filter candidates, but it does not decide what is durable, resolve contradictions, maintain a profile, preserve source provenance, or enforce forgetting. TencentDB Agent Memory, Mem0, and LangMem implement some or all of that policy. Zvec only implements the retrieval/storage layer.

## Candidate Comparison

| Candidate | Layer | Runtime | Main strength | Main issue for Telefire |
| --- | --- | --- | --- | --- |
| TencentDB Agent Memory | Hierarchical memory engine | Node.js sidecar; Python Hermes client | Four-level traceable memory with Markdown personas | Current gateway does not correctly apply per-request user identity and lacks update/delete APIs |
| Mem0 OSS | Memory extraction and retrieval engine | Native sync/async Python | Mature Python API, configurable providers, scoped search, update/delete/history | Uses SQLite for history and has no Zvec adapter or Markdown profile layer |
| Zvec | In-process vector and full-text database | Native Python/C++ binding | Lightweight local hybrid retrieval with Python 3.14 wheels | All memory extraction, consolidation, provenance, and profile behavior must be built |
| LangMem | Memory-policy toolkit | Python with LangChain/LangGraph stack | Profile and collection memory, background consolidation | Large framework dependency for a small Telethon plugin |
| Letta | Full stateful agent runtime | Server/current TypeScript agent SDK | Rich editable memory blocks | Competes with Telefire's agent loop; legacy Python server excludes Python 3.14 |

## TencentDB Agent Memory

Tencent's design is the closest conceptual match to the requested human-readable memory. It stores raw conversations at L0, extracted atomic facts at L1, scene summaries at L2, and a persona at L3. Lower layers retain evidence while upper layers are Markdown. Its official product documentation also describes hybrid keyword/vector retrieval and source links through all four levels. Sources: [official product architecture](https://cloud.tencent.com/document/product/1813/132100), [repository README at the reviewed revision](https://github.com/TencentCloud/TencentDB-Agent-Memory/blob/4339e63650920871eb0e8888083a1779d114e3ae/README.md).

The current open-source package is TypeScript, requires Node.js 22.16 or newer, and defaults to SQLite plus `sqlite-vec`; its Hermes integration runs a local HTTP gateway. It accepts an OpenAI-compatible base URL, key, and model. The reviewed stable release was `v0.3.6`; `v1.0.0` was still marked as a prerelease. Sources: [package metadata](https://github.com/TencentCloud/TencentDB-Agent-Memory/blob/4339e63650920871eb0e8888083a1779d114e3ae/package.json), [gateway configuration](https://github.com/TencentCloud/TencentDB-Agent-Memory/blob/4339e63650920871eb0e8888083a1779d114e3ae/src/gateway/config.ts), [releases](https://github.com/TencentCloud/TencentDB-Agent-Memory/releases).

There is a blocking multi-user integration gap in the reviewed gateway. Request types declare `user_id`, and the standalone adapter provides `buildRuntimeContextForRequest`, but the gateway's recall and capture handlers do not use either one. They call the core with only the query/session or completed turn, leaving the adapter's static `default_user` context in place. The search API also has no user filter, and the gateway exposes no direct memory update or delete route. This makes the stock gateway unsafe for Telefire's per-user and per-chat isolation without a fork or upstream fix. Sources: [request types](https://github.com/TencentCloud/TencentDB-Agent-Memory/blob/4339e63650920871eb0e8888083a1779d114e3ae/src/gateway/types.ts), [gateway handlers](https://github.com/TencentCloud/TencentDB-Agent-Memory/blob/4339e63650920871eb0e8888083a1779d114e3ae/src/gateway/server.ts), [standalone adapter](https://github.com/TencentCloud/TencentDB-Agent-Memory/blob/4339e63650920871eb0e8888083a1779d114e3ae/src/adapters/standalone/host-adapter.ts).

Assessment: excellent architecture to borrow from, but not the first backend to adopt for this multi-user bot.

## Mem0 OSS

Mem0 is a Python-native memory engine. Applications submit conversation turns to `add`, then use `search` before a later model call. It extracts and deduplicates durable facts, embeds them, supports custom extraction instructions, and scopes records with identifiers and metadata. Its async API covers add, search, list, update, delete, scoped delete, and change history. Sources: [how Mem0 works](https://docs.mem0.ai/core-concepts/how-it-works), [async memory](https://docs.mem0.ai/open-source/features/async-memory), [custom instructions](https://docs.mem0.ai/open-source/features/custom-instructions), [metadata filtering](https://docs.mem0.ai/open-source/features/metadata-filtering).

The OpenAI adapters accept custom base URLs for both chat completion and embeddings, so an OpenAI-compatible provider can serve both roles. Sources: [LLM configuration](https://github.com/mem0ai/mem0/blob/c9af55986e4a31aa98931b6b909d5639e9b2013a/mem0/configs/llms/openai.py), [embedding adapter](https://github.com/mem0ai/mem0/blob/c9af55986e4a31aa98931b6b909d5639e9b2013a/mem0/embeddings/openai.py).

The Python package declares Python `>=3.10,<4.0`, although its checked-in development environments cover only 3.10 through 3.12. A local clean import test succeeded on Telefire's Python 3.14.5 with Mem0 `2.0.11`. Source: [package metadata](https://github.com/mem0ai/mem0/blob/c9af55986e4a31aa98931b6b909d5639e9b2013a/pyproject.toml).

Mem0 is not SQLite-free. Its default semantic index is local Qdrant, but Python OSS always constructs a SQLite history/message manager. The history path can be moved or made in-memory, but there is no alternate Python history-store interface in the reviewed configuration. It also does not currently register Zvec as a vector-store provider. Sources: [OSS defaults](https://docs.mem0.ai/open-source/overview), [Qdrant configuration](https://docs.mem0.ai/components/vectordbs/dbs/qdrant), [history configuration](https://github.com/mem0ai/mem0/blob/c9af55986e4a31aa98931b6b909d5639e9b2013a/mem0/configs/base.py), [history implementation](https://github.com/mem0ai/mem0/blob/c9af55986e4a31aa98931b6b909d5639e9b2013a/mem0/memory/storage.py), [provider registry](https://github.com/mem0ai/mem0/blob/c9af55986e4a31aa98931b6b909d5639e9b2013a/mem0/utils/factory.py).

Anonymous PostHog telemetry is enabled by default and should be disabled for this private-memory feature with `MEM0_TELEMETRY=false`. Source: [telemetry implementation](https://github.com/mem0ai/mem0/blob/c9af55986e4a31aa98931b6b909d5639e9b2013a/mem0/memory/telemetry.py).

Assessment: best ready-made fit if SQLite is acceptable only as internal audit/history state and Qdrant is the semantic store. Telefire would still own the Markdown user-profile projection and strict participant-scoping policy.

## Zvec

Zvec is an Apache-2.0 in-process database, not a memory engine. It supports dense and sparse vectors, full-text search, hybrid retrieval, scalar metadata filters, deletion, write-ahead logging, and single-writer/multi-reader operation without a server. Sources: [official quickstart](https://zvec.org/en/docs/db/quickstart/), [repository README](https://github.com/alibaba/zvec/blob/78ef197aaa8e8d618b063d587175820b6bd839ee/README.md).

Its build publishes CPython 3.10 through 3.14 wheels. A local clean import test succeeded on Python 3.14.5 with Zvec `0.5.1`. Source: [build metadata](https://github.com/alibaba/zvec/blob/78ef197aaa8e8d618b063d587175820b6bd839ee/pyproject.toml).

Zvec can hold atomic memories with fields such as Telegram account, chat ID, user ID, scope, source message ID, timestamp, and status. It cannot decide which facts to extract, merge a correction, synthesize a profile, or preserve a readable source trail. Telefire would need to implement those policies and maintain the Markdown profile itself.

Assessment: best strict no-SQLite storage choice, but it turns this feature into a custom memory-engine project rather than an integration.

## Other Similar Systems

LangMem provides useful policy primitives: hot-path tools, a background extractor/consolidator, and both profile-shaped and searchable collection memories. Its functional core can work with custom storage, but the package directly depends on LangChain, LangGraph, LangSmith, OpenAI, and Anthropic integrations. For this small plugin, that is more framework surface than the feature needs. Sources: [official introduction](https://langchain-ai.github.io/langmem/), [repository README](https://github.com/langchain-ai/langmem/blob/c01e273b94aa4c06e41d0ed1ccce0db17de2bc11/README.md), [package metadata](https://github.com/langchain-ai/langmem/blob/c01e273b94aa4c06e41d0ed1ccce0db17de2bc11/pyproject.toml).

Letta has strong editable and shareable memory blocks, but it is a complete stateful-agent runtime rather than an embeddable memory component. The legacy Python server declares Python `>=3.11,<3.14`, and current development has moved toward the TypeScript Letta Agent SDK. Sources: [memory blocks](https://docs.letta.com/guides/core-concepts/memory/memory-blocks), [repository status](https://github.com/letta-ai/letta/blob/b76da9092518cbaa2d09042e52fdcbde69243e18/README.md), [Python constraint](https://github.com/letta-ai/letta/blob/b76da9092518cbaa2d09042e52fdcbde69243e18/pyproject.toml).

## Recommendation

### If SQLite Must Not Be the Semantic Store

Use Mem0 OSS as an in-process `AsyncMemory` engine with local Qdrant for semantic memories. Accept its small SQLite database only for operation history, disable telemetry, and keep Telefire's global profiles as generated Markdown. This gives the desired extraction, retrieval, correction, deletion, and audit behavior with the least custom code.

### If SQLite Must Not Exist Anywhere

Use Zvec and build a small Telefire-owned memory policy:

1. Store atomic facts, episodes, and embeddings in Zvec with namespaced subject and scope fields plus optional origin metadata.
2. Extract candidate memories after successful AI turns using a validated structured response.
3. Resolve add/update/delete operations in application code.
4. Synthesize global user profiles into Markdown and keep chat memories as filtered atomic facts.
5. Preserve immutable source and revision records, either as versioned Zvec documents or a separate append-only journal.

This is controllable and local, but substantially more implementation and evaluation work than Mem0.

### Do Not Select Yet

- Do not adopt TencentDB Agent Memory's stock gateway until per-request user scoping and explicit lifecycle APIs are fixed or deliberately forked.
- Do not adopt Letta; it replaces too much of Telefire's agent loop.
- Do not add LangMem unless later requirements justify adopting LangGraph's storage and execution model.

## Decision

The original Mem0 with local Qdrant decision was superseded on 2026-07-11. User memory will be owned by a shared, standalone HTTP service backed by Zvec, with Telefire acting only as a memory client. The service is the sole writer, while its Python core remains directly usable for tests and isolated deployments. This is recorded in [ADR 0002](../adr/0002-use-a-standalone-zvec-memory-module.md).

## Reassessment: Portable Context Augmentor

The memory module now owns portable memory subjects and scopes, typed fact and episode extraction, profile synthesis, correction policy, and bounded context packing. Mem0's current OSS extraction path is additive-only, so these requirements would still need Telefire-owned policy above it. In a local Python 3.14 import comparison, Mem0 loaded about five times slower and reached roughly 2.6 times Zvec's peak resident memory; this is a directional development-machine measurement, not a production benchmark.

Zvec directly provides the remaining storage operations the module needs: typed scalar fields, vector and full-text search, filtered retrieval, partial update and upsert, deletion, and WAL-backed local durability. The provisional recommendation is therefore Zvec behind a Telefire-owned memory module, using TencentDB Agent Memory's layered, human-readable design as inspiration rather than a runtime dependency. This trades more domain implementation and evaluation work for a smaller runtime, a portable contract, and full control over extraction and lifecycle semantics.
