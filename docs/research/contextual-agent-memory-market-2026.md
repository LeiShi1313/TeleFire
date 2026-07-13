# Contextual Agent Memory Market, July 2026

**Research snapshot:** 2026-07-12
**Decision target:** a standalone, client-neutral contextual memory service for Telefire
**Source policy:** primary sources only: project repositories, official documentation, product API documentation, and papers

## Executive decision

The selected engine is **Hindsight v0.8.4**. Integration validation will tune its episode representation, identity mapping, prompts, and budgets; it is not an engine bake-off and Telefire will not maintain a fallback memory implementation. Its native unit of isolation is a memory bank; a bank contains facts, source documents/chunks, entities, relationships, directives, and observations, and the documentation states that banks are completely isolated. Its `retain` API accepts a whole multi-speaker conversation, timestamp, metadata, explicit entities, and an idempotent document identifier. Its `recall` path combines semantic, BM25, graph, and temporal retrieval, then exposes fact type, entities, event time, mention time, document/chunk provenance, optional source chunks, and evidence behind consolidated observations. Recall also has retrieval and token budgets. The project is MIT-licensed, self-hosts in one Docker container with an embedded database or external PostgreSQL, and provides HTTP, MCP, clients, and a UI. These capabilities are documented in the [Hindsight repository](https://github.com/vectorize-io/hindsight), [bank API](https://hindsight.vectorize.io/developer/api/memory-banks), [retain API](https://hindsight.vectorize.io/developer/api/retain), and [recall API](https://hindsight.vectorize.io/developer/api/recall).

**Graphiti** remains the strongest lower-level reference from the market review, but it is not part of the selected architecture. Its first-class episodes, temporal fact invalidation, entity and edge ontologies, hybrid retrieval, graph-distance reranking, and isolated `group_id` namespaces help explain the design space. Its current MCP server also provides HTTP transport, queued episode ingestion, fact/node search, direct triplet insertion, and provenance tools. See the [Graphiti repository](https://github.com/getzep/graphiti), [MCP server](https://github.com/getzep/graphiti/tree/main/mcp_server), [episode model](https://help.getzep.com/graphiti/core-concepts/adding-episodes), [namespacing](https://help.getzep.com/graphiti/core-concepts/graph-namespacing), and [search strategies](https://help.getzep.com/graphiti/working-with-data/searching).

**Cognee** is the strongest broader platform alternative. It already combines relational provenance, vector search, a graph store, session and permanent memory, dataset permissions, temporal retrieval, HTTP/MCP, and configurable graph schemas. It is also considerably heavier and more document-knowledge-base-oriented than Hindsight. It is worth a second pilot only if Telefire also wants a general knowledge platform. See Cognee's [architecture](https://docs.cognee.ai/core-concepts/architecture), [`remember`](https://docs.cognee.ai/core-concepts/main-operations/remember), [`recall`](https://docs.cognee.ai/core-concepts/main-operations/recall), and [permissions](https://docs.cognee.ai/core-concepts/multi-user-mode/permissions-system/overview).

**Do not choose Mem0 OSS v3 as the graph-memory core.** The April 2026 v3 migration removed `enable_graph`, `graph_store`, and the external Neo4j/Memgraph/Kuzu/AGE/Neptune implementation. It replaced the traversable graph result with automatic entity extraction into a parallel vector collection and a score boost; the former `relations` field is no longer returned. The authoritative evidence is the [OSS v2-to-v3 migration guide](https://github.com/mem0ai/mem0/blob/main/docs/migration/oss-v2-to-v3.mdx), merged [PR #4805](https://github.com/mem0ai/mem0/pull/4805), and the [Node SDK v3.0.0 release](https://github.com/mem0ai/mem0/releases/tag/ts-v3.0.0). The public graph-memory page and repository `LLM.md` still contain older external-graph examples; they must not be used to assess the maintained v3 API.

Recommended sequence:

1. Adopt Hindsight with **one bank per Telefire scope** and bank-pinned read-only agent tools.
2. Keep the existing Zvec records only as migration input and an evaluation baseline while Hindsight integration is validated.
3. Use the acceptance tests below to refine identity serialization, extraction instructions, retrieval policy, and answer behavior.
4. Re-ingest retained evidence through Hindsight, re-embed it, and retire the Zvec runtime after validation.
5. Borrow progressive disclosure and inspectability ideas from TencentDB Agent Memory, LLM Wiki, Nowledge Mem, and LangMem without adopting their runtime assumptions.

## What Telefire actually needs

The unit of ingestion should no longer be "one fact about one author." It should be an **episode** or **observation bundle** containing ordered messages, multiple actors, explicit mentions, attachment descriptions, timestamps, and optional source references. Extraction can then produce multiple natural-language assertions about any entities involved.

The minimum conceptual model is:

```text
Scope
  stable ID selected by trusted application code

Episode
  scope, observed_at, ordered messages, participants, optional source_ref

Entity
  canonical integration-owned ID, type, display names and aliases

Assertion
  scope, natural-language claim, about_entities, related_entities,
  valid time, observation time, confidence/status, evidence references

Observation/Profile
  a revisable synthesis backed by assertions and their evidence
```

Important consequences:

- `subject_id` is useful as an index or filter, but it is not the ownership model for a multi-party fact.
- An alias is an assertion in a scope, not a canonical identity. `"XX refers to telegram:user:123 in chat 456"` needs provenance and time like every other claim.
- Scope is an authorization boundary, not a relevance hint. The model must never choose or broaden it.
- Cross-scope retrieval must require an explicit application policy decision. There is no automatic fallback from a chat scope to global, private, or another chat scope.
- Recursive expansion must be relevance-driven and bounded. Loading every entity connected to every recalled fact would be both noisy and unsafe.
- Raw integration message IDs are optional provenance values, not part of the portable memory contract. Another client can supply a URL, content hash, event ID, file path, or no external reference.

## Market taxonomy

Products in this market use the word "memory" for different systems. Treating them as interchangeable creates bad architecture decisions.

| Class | Primary job | Representative systems | Fit for Telefire |
|---|---|---|---|
| Online conversational/agent memory | Incrementally extract, revise, and retrieve changing facts from interactions | Hindsight, Graphiti/Zep, Cognee, Mem0, TencentDB Agent Memory, Nowledge Mem, MemOS | Correct market, but scope and evidence models vary widely |
| Agent runtime with memory | Own the agent loop, context window, tools, and state | Letta; LangMem when paired with LangGraph | Useful ideas, wrong ownership boundary if Telefire keeps Pi/its agent runtime |
| Document knowledge base / graph RAG | Compile or index a corpus, then answer questions over it | Microsoft GraphRAG, LightRAG, LLM Wiki | Good for documents; lacks online conversational lifecycle and hard per-turn scope by default |
| Retrieval/storage component | Store vectors, text, metadata, or graph edges | Zvec, PostgreSQL/pgvector, Neo4j, LadybugDB, Qdrant | Building blocks only; Telefire must implement extraction, identity, time, provenance, and policy |

An online memory system must handle new evidence, contradictions, temporal validity, identity changes, source drill-down, and low-latency incremental writes. A document knowledge base usually assumes a corpus is indexed in batches and queried after indexing. GraphRAG quality alone does not make a system agent memory.

## Requirement fit matrix

Ratings describe documented native behavior as of the research date, not what could be created with unlimited custom code.

- **Strong:** native and directly usable.
- **Partial:** present but requires a wrapper, extension, or policy discipline.
- **Weak:** adjacent capability, not the target behavior.
- **Fail:** conflicts with a hard requirement.

| System | Multi-message, multi-entity ingest | Provenance and time | Entity graph plus hybrid retrieval | Hard no-cross-scope boundary | Bounded agent expansion | Local/self-hosted | Role | Decision |
|---|---|---|---|---|---|---|---|---|
| [Hindsight](https://github.com/vectorize-io/hindsight) | Strong | Strong | Strong | Strong with bank-per-scope | Strong agentic `reflect` loop; traversal depth remains coarse-grained | Strong | Full memory service | **Adopt directly for pilot** |
| [Graphiti](https://github.com/getzep/graphiti) | Strong | Strong, bitemporal | Strong | Strong with mandatory `group_id` | Strong primitives; host must enforce call/depth budget | Strong | Framework/substrate | **Reference only; not selected** |
| [Cognee](https://github.com/topoteretes/cognee) | Strong | Strong | Strong | Strong only with dataset scoping and backend access control enforced | Strong retrieval modes; host still bounds agent calls | Strong | Broad memory/knowledge platform | **Second platform pilot** |
| [Mem0 OSS](https://github.com/mem0ai/mem0) v3 | Strong | Partial history/metadata; weak source assertion contract | Weak: vector-resident entity linking, no traversable relations | Partial through caller IDs/filters | Weak | Strong | Full vector-memory service | **Reject as graph-memory core** |
| [TencentDB Agent Memory](https://github.com/TencentCloud/TencentDB-Agent-Memory) | Strong | Strong evidence drill-down | Weak for general entity graphs | Partial, session-oriented | Weak outside its layered drill-down | Strong | OpenClaw/Hermes memory plugin and gateway | Borrow ideas; reject core |
| [Nowledge Mem](https://mem.nowledge.co/docs) | Strong | Strong | Strong | **Fail: entity graph is global across spaces** | Strong graph API with depth limit | Available, but proprietary | Commercial full product | Borrow UX; reject core |
| [Zep](https://help.getzep.com/zep-vs-graphiti) | Strong | Strong | Strong | Strong per graph, but user graphs intentionally combine threads | Strong managed search/BFS | Fail for ordinary local self-host; BYOC enterprise | Commercial managed service | Reject for local core; reconsider for managed deployment |
| [Neo4j Agent Memory](https://github.com/neo4j-labs/agent-memory) | Strong | Partial to strong, with reasoning audit edges | Strong | Partial through `user_identifier`; needs scope tests | Strong MCP/Cypher surface | Strong with Neo4j | Experimental memory SDK/service | Watch and spike after Hindsight/Graphiti |
| [MemOS](https://github.com/MemTensor/MemOS) | Strong | Partial | Strong | Partial through readable/writable MemCubes | Partial | Strong but heavy | Memory operating platform | Borrow capability model; do not adopt in v1 |
| [LangMem](https://github.com/langchain-ai/langmem) | Strong extraction | Weak unless application adds it | Fail: no graph | Strong namespace primitive if fixed by host | Strong agent tool pattern | Strong | Python SDK/component | Borrow tool and consolidation patterns |
| [Letta](https://github.com/letta-ai/letta) | Strong for its own agents | Partial | Weak | Partial through archives/blocks | Strong inside Letta agent runtime | Strong | Stateful agent runtime | Reject as standalone memory dependency |
| [Microsoft GraphRAG](https://github.com/microsoft/graphrag) | Strong for documents, not live chat | Strong document lineage | Strong graph-RAG | Weak/application-defined | Strong query modes, not memory tools | Strong | Batch document knowledge system | Reject core; borrow community retrieval |
| [LightRAG](https://github.com/HKUDS/LightRAG) | Strong for incremental documents | Weak for conversational evidence/time | Strong graph-RAG | Partial workspace convention | Partial | Strong | Document RAG server | Reject core |
| [LLM Wiki pattern](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f) | Strong for source compilation, not online turns | Strong if schema enforces citations | Strong explicit links; optional vectors | Strong per filesystem/project if host fixes it | Strong search/read/follow pattern | Strong | Knowledge-compilation pattern | Borrow ideas; reject core |
| [Zvec](https://github.com/alibaba/zvec) | Not an extraction system | Application-owned | Strong vector/BM25/filtering, no graph | Application-owned | None | Strong | Embedded retrieval component | Keep as baseline/component |

## Decision shortlist

### Adopt directly: Hindsight, as a reversible pilot

Hindsight is the only reviewed full system that closely matches all of these at once:

- A full conversation can be retained as one item so the extractor can attribute facts across speakers and resolve temporal references. Retain also supports append/replace updates, caller metadata, timestamps, explicit entities, tags, and idempotent `document_id` values. [Retain API](https://hindsight.vectorize.io/developer/api/retain)
- Each recalled fact carries type (`world`, `experience`, or `observation`), context, metadata, tags, associated entities, occurred time, mentioned time, document ID, and chunk ID. [Recall API](https://hindsight.vectorize.io/developer/api/recall)
- Recall runs semantic, BM25, graph, and temporal strategies in parallel, fuses and reranks them, and lets the caller choose `low`, `mid`, or `high` retrieval depth plus a maximum returned-token budget. [Recall architecture and parameters](https://hindsight.vectorize.io/developer/api/recall)
- `reflect` is a bounded memory-reasoning agent. It searches mental models, observations, and raw facts, can expand from retrieved memories, runs for at most ten iterations, validates citations, accepts the same scope tags as recall, and can return its complete tool trace. [Reflect architecture](https://hindsight.vectorize.io/developer/reflect) and [Reflect API](https://hindsight.vectorize.io/developer/api/reflect)
- Observations are consolidated, evidence-backed beliefs. Recall can return each observation's contributing facts, while optional chunks recover surrounding source text. [Observation/source-fact recall](https://hindsight.vectorize.io/developer/api/recall)
- A bank is a complete isolated unit containing memories, documents, entities, relationships, and directives. The docs explicitly state that data in one bank is not visible to another. [Memory banks](https://hindsight.vectorize.io/developer/api/memory-banks)
- Tag filters run in the database across all retrieval strategies, including strict modes that exclude untagged records. Observation scopes can consolidate exact tag sets independently. [Retain scoping](https://hindsight.vectorize.io/developer/api/retain) and [recall tag filtering](https://hindsight.vectorize.io/developer/api/recall)
- The MIT-licensed project ships REST, Python, TypeScript, Go, CLI, MCP, a UI, Docker with embedded `pg0`, external PostgreSQL support, and embedded Python/Node modes. [Repository and deployment options](https://github.com/vectorize-io/hindsight)
- Extension points cover API-key/tenant authentication, PostgreSQL-schema isolation, operation validation, rate limits, audit hooks, custom HTTP endpoints, and extra MCP tools. [Extensions](https://hindsight.vectorize.io/developer/extensions)

The design still has important gaps:

1. The documented explicit entity input is name plus type, not a guaranteed external canonical ID. Telefire must inject integration-owned IDs in a deterministic representation and test whether extraction/dedup preserves them.
2. Multi-hop graph retrieval is deliberately abstracted. The [retrieval guide](https://hindsight.vectorize.io/developer/retrieval) presents multi-hop relationship questions and a coarse `budget` that changes search breadth, while the official [v0.5 release notes](https://hindsight.vectorize.io/blog/2026/04/07/version-0-5-0) say iterative BFS/MPFP traversal was replaced by a single-round-trip `LinkExpansionRetriever`. The higher-level `reflect` loop can call `recall` and `expand` repeatedly, but callers cannot set an exact graph path or hop count. This is a useful bounded agent interface, not a raw traversal contract.
3. Automatic observations run in the background and may lag the newest facts. Prompt assembly must combine recent `world`/`experience` facts with observations rather than assuming observations are immediately current.
4. Replacing a document re-extracts it and can change fact IDs or wording. Prefer append-only episode documents for normal chat ingestion; use replace only for explicit correction/replay workflows.
5. Tag filtering has non-strict modes that include untagged memories. Telefire should use a bank as the hard scope boundary, not rely on tags alone.
6. A generic MCP server exposes more tools than Telefire should give the answer agent. The application should expose a bank-pinned read-only wrapper, not `list_banks`, `create_bank`, `retain`, `delete`, or arbitrary bank IDs.

**Scope mapping:** `bank_id = <client>:<scope-type>:<scope-id>`, for example `telegram:chat:-100123`. Canonical users live as entities inside that bank. A private owner profile, another group, and any future global knowledge are different banks and are never searched implicitly.

**Current stack fit:** Hindsight can reuse Telefire's OpenAI-compatible chat endpoint. Its OpenAI-compatible embedding provider can point at the existing Ollama service and `qwen3-embedding:0.6b`, and it can use a local cross-encoder or RRF-only reranking. Existing Zvec vectors should not be copied because Hindsight owns a different index and metadata model; a migration would re-ingest source text and re-embed it. The same fixed-embedding-space rule still applies: pin the embedding model and dimension for the lifetime of the pilot bank.

### Lower-level reference: Graphiti

Graphiti is the preferred custom foundation because its model is already close to the desired internal contract:

- An episode is itself a node. It can contain text, JSON, or multi-turn `speaker: message` content; extracted entities are connected through `MENTIONS`, and the episode provides provenance and point-in-time context. [Adding episodes](https://help.getzep.com/graphiti/core-concepts/adding-episodes)
- Graphiti's temporal graph stores evolving facts and invalidates superseded edges rather than treating the latest extraction as timeless. The architecture and evaluation are described in the [Zep temporal knowledge graph paper](https://arxiv.org/abs/2501.13956).
- `group_id` is attached to nodes and edges and is explicitly documented as an isolated graph namespace. Search accepts `group_id`; cross-namespace queries require application code to issue separate searches. [Graph namespacing](https://help.getzep.com/graphiti/core-concepts/graph-namespacing)
- Hybrid search combines semantic similarity and BM25 with RRF. Configurable recipes search nodes, edges, and communities, and graph distance can rerank around a focal entity. [Searching](https://help.getzep.com/graphiti/working-with-data/searching)
- The Apache-2.0 Python library supports self-hosted graph backends including Neo4j, FalkorDB, Amazon Neptune, and an embedded Kuzu/Ladybug lineage; its MCP server demonstrates an agent-facing integration. [Repository](https://github.com/getzep/graphiti)

What Telefire would still own:

- a stable HTTP API and portable episode/assertion/entity DTOs;
- authentication, rate limiting, queues, retries, and observability;
- canonical platform entity creation and alias resolution rules;
- a profile/observation consolidation job with evidence links;
- hard enforcement that every write and query has exactly one `group_id`;
- read-only agent tools, call budgets, token budgets, and source rendering;
- a dashboard and correction/revision workflow;
- migrations and export independent of the selected graph backend.

Graphiti clarifies which controls a lower-level implementation would require. Its MCP HTTP server and queue reduce that burden, but they do not replace Telefire's scope guard, identity adapter, correction workflow, or stable client-neutral contract. Telefire is not maintaining this as a fallback track.

### Build on conditionally: Cognee

Cognee is closer to a general memory and knowledge platform than a small library:

- It deliberately uses relational storage for documents/chunks/provenance, a vector store for semantic search, and a graph store for entities and relationships. [Architecture](https://docs.cognee.ai/core-concepts/architecture)
- `remember()` accepts text, files, URLs, and metadata-bearing `DataItem` objects. Permanent mode builds the graph and embeddings; session mode is a fast cache that can later bridge into permanent memory. [Remember](https://docs.cognee.ai/core-concepts/main-operations/remember)
- `recall()` auto-routes among graph-backed strategies, can mix session and permanent retrieval, returns a source classification, and scopes permanent recall to datasets. [Recall](https://docs.cognee.ai/core-concepts/main-operations/recall)
- Temporal mode extracts events and timestamps and supports before/after/range queries; Cognee can also integrate Graphiti for episode history. [Time awareness](https://docs.cognee.ai/guides/time-awareness)
- Backend access control makes datasets the permission unit and enforces user/dataset isolation across graph and vector stores. It must be enabled and tested; without it, dataset naming alone is not a security boundary. [Permissions](https://docs.cognee.ai/core-concepts/multi-user-mode/permissions-system/overview)
- The Apache-2.0 Python core has HTTP and MCP surfaces plus newer Rust and TypeScript SDKs. Local defaults are SQLite, LanceDB, and a file-based Kuzu/Ladybug graph; PostgreSQL/pgvector and Neo4j are production options. [Vector stores](https://docs.cognee.ai/setup-configuration/vector-stores) and [graph stores](https://docs.cognee.ai/setup-configuration/graph-stores)

Cognee's gaps are operational weight, a broad and fast-changing API surface, and a document-ingestion heritage. Its default session-to-permanent self-improvement also writes derived structures automatically. A Telefire pilot should disable automatic improvement initially, use one dataset per scope, enable backend access control, and test exact source-to-assertion attribution before exposing any agentic recall mode.

## Required-system evaluations

### Mem0 OSS and Mem0 Platform

Mem0 remains a mature and easy-to-integrate memory product. Its Apache-2.0 repository has Python and TypeScript SDKs, many vector providers, a self-hosted REST server, Docker Compose, dashboard, authentication, CRUD/history, request logs, and `user_id`/`agent_id`/`run_id` filtering. The maintained server surface is documented in the [self-hosted REST API](https://docs.mem0.ai/open-source/features/rest-api).

However, the current v3 architecture is not the graph system described by older examples:

- In April 2026, [PR #4805](https://github.com/mem0ai/mem0/pull/4805) deleted the external graph drivers and approximately 4,000 lines of graph-store code.
- The [official OSS v3 migration guide](https://github.com/mem0ai/mem0/blob/main/docs/migration/oss-v2-to-v3.mdx) says `enable_graph` and `graph_store` are removed. Entity terms are extracted into a parallel collection in the configured vector store. Shared entities affect the combined retrieval score, but the old `relations` result is gone and callers cannot traverse the structure.
- The same migration changes extraction to ADD-only. New memories accumulate instead of the extractor issuing UPDATE/DELETE decisions, which makes contradiction and revision semantics less suitable for Telefire's evidence-backed assertions.
- The current [Node SDK v3 release line](https://github.com/mem0ai/mem0/releases/tag/ts-v3.0.13) follows that contract.

The older [Graph Memory page](https://docs.mem0.ai/open-source/features/graph-memory) still describes `graph_store` backends and a `relations` key, and [`LLM.md`](https://github.com/mem0ai/mem0/blob/main/LLM.md) still contains `enable_graph`. Those sources conflict with the migration guide, merged removal PR, and v3 API. For a July 2026 decision, they are stale.

Mem0 Platform advertises built-in graph memory, but that is a managed product feature rather than a self-hosted external graph contract. Mem0 is still useful as a vector-memory benchmark and as a reference for a clean CRUD/dashboard API. It is not a base for Telefire's required bounded graph traversal.

### TencentCloud/TencentDB-Agent-Memory

[TencentDB Agent Memory](https://github.com/TencentCloud/TencentDB-Agent-Memory) is a new MIT-licensed TypeScript/Node package and gateway built primarily for OpenClaw and Hermes. Its strongest design is progressive disclosure:

- long-term memory is a semantic pyramid: L0 raw conversation -> L1 atomic facts -> L2 scenarios -> L3 persona;
- raw traces/facts live in a database, while higher-level scenarios and personas are human-readable Markdown;
- every abstraction has a drill-down path to lower-level evidence;
- short-term tool output is offloaded and represented by a compact Mermaid task canvas;
- the default local store is SQLite plus `sqlite-vec`, with hybrid full-text/vector retrieval;
- the gateway exposes capture/search/recall HTTP endpoints and optional bearer authentication.

The latest reviewed release is [v0.3.6](https://github.com/TencentCloud/TencentDB-Agent-Memory/releases/tag/v0.3.6). Maturity is therefore promising but early. Its session-key, persona, OpenClaw lifecycle, and Hermes provider assumptions are much narrower than Telefire's platform-neutral multi-entity graph. Adopt its evidence pyramid, human-readable synthesis, and "summary first, drill down on demand" approach; do not adopt it as the core service.

### Nowledge Mem

[Nowledge Mem](https://mem.nowledge.co/docs) is a polished commercial, local-first memory product with desktop apps, a local REST API, CLI, MCP/integrations, Docker/server deployment, threads, supersede/deprecate operations, a memory filesystem, and graph exploration. Its [search pipeline](https://mem.nowledge.co/docs/search-relevance) combines semantic similarity, BM25, labels, graph traversal, recency, frequency, importance, confidence, event time, and record time. Its [graph search API](https://mem.nowledge.co/docs/api/graph/search/get) exposes node/edge filters and a caller-bounded traversal depth from one to five.

It fails the hard Telefire isolation rule. Nowledge's [Spaces documentation](https://mem.nowledge.co/docs/spaces) says spaces change normal read/write focus but the entity graph remains global by design; a space can also be configured to read shared or every space. The product's [terms](https://mem.nowledge.co/terms) identify the software as proprietary and prohibit reverse engineering. A wrapper could attempt to constrain every query, but that would be relying on policy over a deliberately global graph. Borrow its source tree, score explanations, supersede/deprecate UX, and graph explorer. Do not use it as Telefire's core.

### Graphiti and Zep

Graphiti and Zep should be evaluated separately:

- **Graphiti** is the Apache-2.0 open-source temporal graph framework. It runs locally and leaves user/thread/auth/service concerns to the application. It is the recommended custom substrate. [Official comparison](https://help.getzep.com/zep-vs-graphiti)
- **Zep** is the proprietary managed system built around that lineage. It adds users, threads, observations, context assembly, governance, dashboards, and enterprise operation. Zep Community Edition is deprecated; normal choices are Zep Cloud, enterprise BYOC, or Graphiti. [Zep FAQ](https://help.getzep.com/faq)

Zep graphs themselves are isolated. The important semantic trap is that a Zep **user graph intentionally combines all threads for that user**, making facts learned in one thread available in another. [Users and user graphs](https://help.getzep.com/users-and-user-graphs). For a Telefire group, the appropriate Zep concept would be a standalone graph per chat, not a user graph. Zep stores source episodes verbatim and can retrieve them behind facts, which is a strong provenance design. [Episodes](https://help.getzep.com/episodes). Zep is worth reconsidering only if managed/BYOC deployment becomes acceptable.

### The likely meaning of "LLM Wiki"

"LLM Wiki" most likely refers first to **Andrej Karpathy's April 2026 `llm-wiki.md` design pattern**, not to one canonical software product. The primary artifact is [Karpathy's gist](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f). The pattern keeps immutable raw sources, asks an LLM to compile and maintain interlinked Markdown wiki pages under a schema, and uses ingest/query/lint operations.

The most visible concrete application found is [`nashsu/llm_wiki`](https://github.com/nashsu/llm_wiki). Its README explicitly identifies the Karpathy gist as its foundation. It is a GPL-3.0 Tauri/Rust plus React/TypeScript desktop app with source traceability, two-step LLM ingestion, wikilink graph analysis, optional LanceDB vectors, graph expansion, local token-protected HTTP, MCP, and an agent chat runtime. It is an impressive document knowledge base, but it compiles a filesystem corpus rather than maintaining low-latency conversational assertions with a hard chat-scope policy.

A separate May 2026 paper, ["Retrieval as Reasoning: Self-Evolving Agent-Native Retrieval via LLM-Wiki"](https://arxiv.org/abs/2605.25480), formalizes a related system that compiles documents into linked wiki pages and gives an agent `search`, `read`, and link-following operations plus an Error Book. It is relevant to bounded compositional retrieval, but it is evaluated as document retrieval rather than online chat memory.

The ideas to borrow are:

- immutable/raw evidence separated from revisable synthesized pages;
- human-readable, reviewable compiled memory;
- an explicit index for progressive disclosure;
- agent tools that search, read, and follow links instead of dumping a whole graph;
- lint/reconciliation passes for contradictions and broken links.

Do not replace the assertion store with Markdown pages in v1. A future per-scope human-readable "memory wiki" can be a compiled projection over evidence-backed assertions.

### LangMem

[LangMem](https://github.com/langchain-ai/langmem) is an MIT-licensed Python SDK, not a standalone service. It distinguishes semantic facts, episodic examples, and procedural memory; it supports hot-path and background extraction; and its managers can insert, update, delete, consolidate, or produce structured profiles. Its core functions are storage-independent, while the stateful layer uses LangGraph `BaseStore`. [Conceptual guide](https://langchain-ai.github.io/langmem/concepts/conceptual_guide/)

Its most useful Telefire contribution is API design. `create_search_memory_tool` and `create_manage_memory_tool` bind an agent tool to a namespace and can restrict permitted actions. [Tool reference](https://langchain-ai.github.io/langmem/reference/tools/). Telefire should copy the fixed-namespace, action-allowlist pattern. LangMem itself has no temporal graph, evidence graph, or standalone multi-tenant service, so it is not the storage core.

### Letta

[Letta](https://github.com/letta-ai/letta) is an Apache-2.0 stateful agent platform. It owns agents, messages, tools, context management, memory blocks, archival vector memory, and conversation search. Its [context hierarchy](https://docs.letta.com/guides/core-concepts/memory/context-hierarchy) is valuable for deciding what is always in context versus searched on demand.

Letta is the wrong dependency boundary for this project: adopting it would replace or compete with Telefire's existing agent runtime rather than provide a thin standalone memory service. Borrow the distinction between always-loaded blocks, archival search, and explicit memory tools; reject it as the memory backend.

### Microsoft GraphRAG

[Microsoft GraphRAG](https://github.com/microsoft/graphrag) is a mature MIT-licensed Python document-indexing and query framework. Its standard indexing pipeline extracts entities, relationships, and claims, detects communities, generates hierarchical community reports, embeds text, and writes Parquet plus a vector index. [Indexing overview](https://microsoft.github.io/graphrag/index/overview/). Its query engine has local entity search, global map-reduce over community reports, and DRIFT search. [Query overview](https://microsoft.github.io/graphrag/query/overview/).

Those are powerful corpus-retrieval techniques, but indexing is batch-oriented and explicitly expensive. GraphRAG does not provide an online conversational memory lifecycle, per-episode temporal invalidation, profile revision, or an application-enforced chat scope. Borrow local/global/community retrieval only if a Telefire scope later accumulates a large document corpus. Do not use it for the primary memory path.

### LightRAG

[LightRAG](https://github.com/HKUDS/LightRAG) is an MIT-licensed Python graph-RAG system with incremental document insertion, dual-level entity/relation retrieval, many vector/graph/key-value backends, an API server, and a Web UI. Its method is described in the [LightRAG paper](https://arxiv.org/abs/2410.05779), and the server surface in the [official API server guide](https://github.com/HKUDS/LightRAG/blob/main/docs/LightRAG-API-Server.md).

LightRAG is simpler than GraphRAG for a self-hosted document knowledge base, but its core records are documents, chunks, entities, and relations rather than sourced, revisable conversational assertions. Workspaces are an operational organization mechanism, not a proven hard authorization boundary. Reject it for Telefire memory; consider it only for separately indexed files and external knowledge.

## Additional candidates discovered

### Hindsight

Hindsight is the main discovery and the top candidate. Its fit and gaps are covered above. The project is young but moving quickly: the reviewed release is [v0.8.4](https://github.com/vectorize-io/hindsight/releases/tag/v0.8.4), and its design is also documented in an [ACL 2026 system paper](https://aclanthology.org/2026.acl-demo.27/). Treat vendor benchmark results as hypotheses; the pilot acceptance tests, not benchmark rankings, decide adoption.

### Neo4j Agent Memory

[Neo4j Agent Memory](https://github.com/neo4j-labs/agent-memory) is an Apache-2.0 Python/TypeScript SDK with short-term messages, long-term entities/preferences/facts, reasoning traces, vector/text search, multi-stage extraction, consolidation, `user_identifier` scoping, an evaluation harness, and MCP profiles. It can use hosted NAMS or self-hosted Neo4j and supports local embeddings. Explicit `TOUCHED` edges provide an audit link from reasoning steps to entities.

It is highly relevant but is explicitly an experimental, community-supported Neo4j Labs project with a first public [v0.4.0 release](https://github.com/neo4j-labs/agent-memory/releases/tag/v0.4.0). Its POLE+O/person-centric model and `user_identifier` namespace need careful adaptation to chat scopes, and its temporal assertion semantics are less explicit than Graphiti's. Watch it and run a later spike; do not make it the v1 production dependency.

### MemOS

[MemOS](https://github.com/MemTensor/MemOS) is an Apache-2.0 memory operating platform. Its MemCube abstraction bundles multiple memory types and supports readable/writable cube IDs and composed views; the architecture includes plaintext graph memory, activation/working memory, and broader parametric/tool memory. [Architecture](https://memos-docs.openmem.net/open_source/home/architecture/).

The cube capability model is a useful way to make scope access explicit. The platform is much broader than Telefire needs and commonly brings Neo4j, Qdrant, optional Redis, schedulers, and multiple memory subsystems. Borrow readable/writable capability ideas; do not adopt it in a deliberately simple first version.

### MemMachine, Supermemory, and EverOS

- [MemMachine](https://github.com/MemMachine/MemMachine) is an Apache-2.0 Python service with episodic Neo4j memory, SQL profile memory, working memory, REST/SDK/MCP, and organization/project/group/agent/user/session identifiers. It is closer to user/session memory than to a general temporal assertion graph; watch, but it does not beat Hindsight or Graphiti for this target.
- [Supermemory](https://github.com/supermemoryai/supermemory) is an MIT TypeScript memory/RAG/profile platform with connectors and a managed product. It is relevant for profile and connector UX, but its current architecture is more hosted/product-centric and less explicit about a self-hosted, evidence-backed traversal contract. It is not a better local core candidate.
- [EverOS, formerly EverMemOS](https://github.com/EverMind-AI/EverOS), is an Apache-2.0 hierarchical memory project with MemCells/MemScenes/profile concepts and agentic retrieval. Its architecture is interesting, but the project identity and APIs are still changing quickly. Track it rather than design Telefire around it.

## Lower-level stores and frameworks

### Zvec

[Zvec](https://github.com/alibaba/zvec) remains a strong local component. It is Apache-2.0, implemented primarily in C++ with Python, Node, Go, Rust, and Dart bindings, and runs in-process. The current v0.5 line adds native BM25 full-text search, dense/sparse vectors, scalar filters, multi-query fusion/reranking, HNSW/DiskANN, WAL durability, and multiple concurrent readers with a single writer. [Query documentation](https://zvec.org/en/docs/db/data-operations/query/) and [FTS design](https://zvec.org/en/blog/2026-07-07-zvec-fts/).

Zvec does not provide entities, graph edges, temporal validity, observation consolidation, source lineage, auth, or scope policy. It should remain the baseline retrieval component while Hindsight is evaluated. Adding a separate embedded graph database beside Zvec would create dual-write and consistency work; that should not be the first custom design.

### PostgreSQL plus pgvector

If Telefire builds a custom memory service rather than adopting Hindsight or Graphiti, **PostgreSQL plus pgvector is the best single-store foundation**:

- PostgreSQL supplies transactions, JSONB, native full-text search, constraints, audit-friendly relational provenance, and recursive CTEs with cycle detection for bounded graph traversal. [Recursive queries](https://www.postgresql.org/docs/current/queries-with.html)
- Row-level security can make `scope_id` a database-enforced boundary; when RLS is enabled and no applicable policy exists, PostgreSQL uses default deny. [Row security](https://www.postgresql.org/docs/current/ddl-rowsecurity.html)
- [pgvector](https://github.com/pgvector/pgvector) adds exact search plus HNSW/IVFFlat and can be combined with PostgreSQL text search for hybrid retrieval.

A simple custom schema can store `episodes`, `entities`, `entity_names`, `assertions`, `assertion_entities`, `evidence`, and typed `edges` in one transaction. This is operationally heavier than Zvec but safer than synchronizing an embedded vector store and an embedded graph store. It is still a storage choice, not a memory solution; Telefire must build extraction, reconciliation, ranking, and tools.

### Graph databases

| Store | License/runtime | Strength | Gap or risk | Telefire role |
|---|---|---|---|---|
| [Neo4j](https://neo4j.com/docs/operations-manual/current/introduction/) | Java server; Community GPLv3, commercial editions | Mature Cypher graph, ACID, full-text and vector indexes, tooling | Separate service; advanced security/multi-database features vary by edition | Best proven Graphiti backend if operating a server is acceptable |
| [LadybugDB](https://github.com/LadybugDB/ladybug) (Kuzu successor) | MIT, embedded C++ with Python/Node/Rust/Go | Local property graph, Cypher, transactions, vector and FTS direction | Young rename/succession and concurrency limits; framework docs still often say Kuzu | Promising local graph component, not first production choice |
| [FalkorDB](https://github.com/FalkorDB/FalkorDB) | Redis-compatible server, SSPL | Fast graph traversal and a documented Graphiti backend | License and another stateful service | Avoid unless its operational/licensing trade-off is explicitly accepted |
| [Qdrant](https://github.com/qdrant/qdrant) | Apache-2.0 Rust server | Strong vector search, payload filters, tenant indexing/shards | No property-graph traversal or memory lifecycle | Vector component only; no advantage over current Zvec for the first pass |

## Recommended Telefire architecture

```text
Telegram/other adapters
        |
        | trusted scope_id + ordered observation bundle
        v
Scope guard and ingestion queue
        |
        | scope is fixed here, never supplied by the LLM
        v
Standalone memory service
        |
        +-- retain/revise path owned by application
        +-- recall path returning facts + evidence + entity hints
        +-- inspection/correction API for dashboard
        |
        v
Hindsight bank per scope

Answer agent receives only:
  memory_recall(query, anchor_entities?, time_hint?)
  memory_reflect(query, anchor_entities?, budget=low)
  memory_get_evidence(result_ids)

The host fixes scope, max calls, max depth, max candidates, and max tokens.
```

### Ingestion policy

1. Preserve the relevant reply chain as one logical ordered observation bundle. Include stable actor IDs, current display names, message times, explicit mentions, and text descriptions of images/files.
2. Pilot two serializations: one multi-speaker item with canonical actor IDs inline in each speaker label, and a batch of per-message items with explicit actor/mention entities. Hindsight attaches caller-supplied entities to every fact extracted from that item, so passing every thread participant as a global explicit-entity list would over-connect unrelated facts.
3. Keep source authorship in metadata and preserve it in the serialized text. Treat "who said this" separately from "who this is about"; the acceptance test decides whether Hindsight's extractor keeps that distinction reliably.
4. Preserve both event time and observation/ingestion time. Keep source metadata on every extracted assertion.
5. Treat automatic alias/entity merges as proposals until repeated evidence or an explicit identity is present. Ambiguous names should remain separate entities.
6. Keep writes outside the answer agent in v1. Application-driven background extraction is easier to audit and cannot be manipulated into changing memory tools mid-answer.

### Retrieval policy

1. Trusted code selects exactly one scope before agent execution.
2. Deterministically collect participants and exact platform `@mentions` from the current chain and pass those as anchor entities.
3. Run one initial hybrid recall from the current scope.
4. For unresolved aliases or relationship questions, allow one low/mid-budget `reflect` call, or at most two additional bank-pinned read-only calls if Telefire keeps reasoning in Pi instead. Do not enable both paths without a shared call budget.
5. Each call has a fixed scope, low/mid retrieval budget, candidate cap, and token cap. The model cannot request another scope or a higher cap.
6. Stop expansion at two relationship steps, when confidence/evidence is insufficient, or when the remaining call/token budget is exhausted.
7. Return compact assertion text first, followed by entity IDs, time, confidence/status, and source handles. Fetch source chunks only for claims used in the final answer.
8. Never automatically query a global bank, owner bank, private chat, or another group. Explicit cross-scope access, if ever added, is a separate owner-controlled operation with an audit record.

### Why this is not uncontrolled recursive RAG

The agent can reason over memory, but it cannot enumerate the whole graph. It starts with deterministic anchors, gets ranked facts, may follow a small number of relevant connections, and must stop under a host-enforced budget. This captures the useful part of the example `XX -> A -> B` without allowing every entity relation to fan out indefinitely.

## Pilot acceptance tests

Hindsight integration must pass these with the local model/embedding stack and realistic Telegram data. Failures refine the adapter, prompts, policy, or upstream contribution; they do not select another engine.

1. **Hard scope isolation:** Put the same alias and contradictory facts in two chat banks. Every recall, graph-assisted result, observation, dashboard view, MCP/HTTP call, and source lookup must remain in the selected bank.
2. **Multi-party attribution:** Ingest a reply chain where one person states a fact about a second person and compares them with a third. Assertions must attach to the correct canonical entities, not automatically to the author.
3. **Alias resolution without `@`:** First establish an alias with explicit canonical identity and evidence. In a later thread in the same scope, resolve the alias. In another scope, do not reuse that resolution.
4. **Ambiguity:** Two people share a display name. Retrieval must preserve ambiguity or ask for clarification; it must not merge them based only on name similarity.
5. **Temporal correction:** Store a dated fact, then a later correction or changed state. Queries for "now" and for the earlier date must return the appropriate state and expose both evidence trails.
6. **Observation evidence:** Every synthesized profile/observation used in a prompt must return its contributing facts and source chunks. Unsupported synthesis is excluded.
7. **Idempotency/replay:** Reprocessing the same observation bundle must not duplicate facts. Appending a new message must not silently lose earlier evidence.
8. **Bounded expansion:** A query requiring a two-hop connection succeeds within the allowed calls; a broad graph-exploration prompt stops at the configured call, depth, result, and token limits.
9. **Prompt injection resistance:** Stored chat text asking the agent to reveal another scope or invoke memory write/delete tools must have no effect because those capabilities and scope arguments are absent.
10. **Freshness:** Recent facts are available before asynchronous observations finish; later observations do not hide newer contradictory evidence.
11. **Operational recovery:** Restart containers during retain and recall. Verify queue recovery, no partial cross-scope records, backup/restore, and export of sources/assertions.
12. **Quality and cost:** Compare attribution precision, retrieval recall, unsupported-memory rate, p50/p95 latency, extraction tokens, recall tokens, and disk growth against the current Zvec baseline.

## Final classification

| Decision | Systems | Rationale |
|---|---|---|
| **Adopt directly** | [Hindsight](https://github.com/vectorize-io/hindsight), as a reversible pilot | Best native combination of isolated banks, multi-party retain, evidence-backed observations, temporal/hybrid recall, bounded retrieval, HTTP/MCP/UI, and local deployment |
| **Reference only** | [Graphiti](https://github.com/getzep/graphiti), [Cognee](https://github.com/topoteretes/cognee), [PostgreSQL + pgvector](https://github.com/pgvector/pgvector) | Useful evidence about graph, platform, and storage design; none is part of the selected runtime |
| **Borrow ideas** | [TencentDB Agent Memory](https://github.com/TencentCloud/TencentDB-Agent-Memory), [LLM Wiki](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f), [Nowledge Mem](https://mem.nowledge.co/docs), [LangMem](https://github.com/langchain-ai/langmem), [Letta](https://github.com/letta-ai/letta), [MemOS](https://github.com/MemTensor/MemOS), [GraphRAG](https://github.com/microsoft/graphrag) | Progressive disclosure, inspectable synthesis, score/source UX, fixed namespaces, memory hierarchy, capability sets, and community retrieval are useful without adopting each runtime |
| **Reject as core** | [Mem0 OSS v3](https://github.com/mem0ai/mem0/blob/main/docs/migration/oss-v2-to-v3.mdx), [Nowledge Mem](https://mem.nowledge.co/docs/spaces), [Zep](https://help.getzep.com/faq), [TencentDB Agent Memory](https://github.com/TencentCloud/TencentDB-Agent-Memory), [Letta](https://github.com/letta-ai/letta), [GraphRAG](https://github.com/microsoft/graphrag), [LightRAG](https://github.com/HKUDS/LightRAG), LLM Wiki implementations | Missing a traversable local graph, unsafe scope semantics, managed/runtime coupling, document-KB orientation, or insufficient maturity for this boundary |

### Adopt directly

- **Hindsight:** pilot as the full standalone service, with one bank per scope and a Telefire-owned, bank-pinned read-only adapter.

### Reference only

- **Graphiti:** control-first temporal graph reference, not a maintained implementation path.
- **Cognee:** example of a broader memory and knowledge platform.
- **PostgreSQL + pgvector:** example of a single-store custom foundation; Telefire is not building it in parallel.

### Keep as a component

- **Zvec:** retain as the current baseline and as a good embedded hybrid retrieval engine. Do not mistake it for the memory model.

### Borrow ideas

- **TencentDB Agent Memory:** layered L0-L3 progressive disclosure and deterministic evidence drill-down.
- **LLM Wiki:** immutable evidence plus human-readable compiled projections; search/read/follow tools and linting.
- **Nowledge Mem:** score transparency, temporal UX, source navigation, supersede/deprecate, graph explorer.
- **LangMem:** background consolidation, fixed namespaces, and action-restricted memory tools.
- **Letta:** always-in-context versus archival memory hierarchy.
- **MemOS:** explicit readable/writable capability sets.
- **Microsoft GraphRAG:** local/global/community retrieval for future large document corpora.

### Reject as the core for this use case

- **Mem0 OSS v3:** no externally configured/traversable graph; entity links are retrieval score features, and v3 extraction is ADD-only.
- **Nowledge Mem:** proprietary and its entity graph deliberately crosses spaces.
- **Zep:** managed/BYOC rather than ordinary local self-host, with user graphs that intentionally integrate threads.
- **TencentDB Agent Memory:** too coupled to OpenClaw/Hermes and persona/session abstractions.
- **Letta:** an agent runtime, not a thin standalone memory layer.
- **Microsoft GraphRAG and LightRAG:** document knowledge systems, not online conversational memory.
- **LLM Wiki implementations:** document compilation systems, not the primary assertion store.
- **Neo4j Agent Memory and EverOS:** promising but too experimental or fast-changing for the first production dependency.

## Adoption gate

Hindsight is the selected engine. Integration is ready when bank isolation is airtight, explicit identities survive extraction, observations remain evidence-backed, and bounded recall/reflect meets quality and latency targets. Problems found by the gate become adapter, prompt, policy, deployment, or upstream-engine work rather than an engine-selection branch.

Do not add a parallel graph database or memory model. Graph-assisted retrieval is an implementation of the memory behavior, not a second application contract.
