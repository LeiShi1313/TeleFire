---
status: ready-for-agent
type: specification
decision: ADR-0005
---

# Hindsight Contextual Chat Memory

## Problem Statement

Telefire's current memory model stores independently extracted facts and episodes under one subject and retrieves them only for the requester and people already present in a reply chain. This makes basic preference recall possible, but it cannot reliably represent a multi-person conversation, distinguish the speaker from the person being discussed, resolve a scoped nickname, follow a relevant relationship, or understand that a plan or claim changed over time.

The current capture path also makes reply participation look like a memory requirement. Ordinary standalone messages are not learned unless they later enter an AI or explicit memory flow, even though a single message can be meaningful evidence. The storage model loses the surrounding conversation that an extractor needs for attribution, quotation, coreference, uncertainty, and temporal interpretation.

Users want memory to feel natural rather than profile-driven. In a memory-enabled chat, Telefire should remember eligible conversation, understand local names and relationships, recover relevant evidence when somebody asks `/ai`, and answer with calibrated confidence. It must do this without searching another chat, turning rumors or jokes into facts, storing model output as evidence, or giving the answer agent write access to memory.

The implementation must remain simple enough to operate in the existing local Docker Compose stack. It should use Hindsight as the selected memory engine instead of building a second graph, alias table, profile store, or fallback backend in Telefire.

## Solution

Replace the subject-scoped Zvec runtime with Hindsight and map each chat, workspace, private conversation, or Saved Messages trust boundary to one isolated Hindsight bank. Store ordered, multi-actor conversation episodes with stable actor identities, display names, timestamps, replies, quotations, explicit mentions, attachment descriptions, and source provenance. Hindsight owns extraction, entities, relationships, temporal memory, observations, recall, reflection, and source evidence.

Make capture opt-in per scope and independent from authorization to use `/ai`. Every eligible human message can be retained, including a standalone message with no reply. Reply structure only determines how messages are grouped into an episode document so the extractor receives useful context.

Feed one idempotent episode pipeline from three entry points: successful `/ai` conversations in memory-enabled scopes, explicit owner `/ai_memory` operations, and a scheduled Telegram Dream Cycle over enabled scopes. Use stable document identities, content versions, ingestion receipts, cursor overlap, and settlement delay so retries, overlapping scans, edits, and later replies converge without duplicating memory.

For each AI Request, trusted Telefire code fixes the active bank and performs one bounded recall using the prompt, reply context, deterministic participants, and exact platform mentions. The Pi Agent Engine may use one bank-pinned, read-only reflection operation when implicit identity, changing state, or a relevant relationship requires additional reasoning. It can fetch bounded source evidence for memories it actually uses. It cannot select a bank, write or delete memory, enumerate banks, or raise retrieval budgets.

Keep remembered evidence separate from Runtime Inference. The answer may relate several remembered facts to make a useful recommendation, but that inference is not automatically retained. Preserve attribution, time, uncertainty, negation, quotation, and relationship direction in both storage and answers.

Deploy Hindsight alongside Telefire and the existing local embedding service. Re-ingest source evidence from the old Zvec store, re-embed it, then retire Zvec from the runtime. Do not dual-write or maintain a fallback implementation.

## User Stories

1. As a chat owner, I want to explicitly enable memory for a chat, so that ordinary conversation is captured only where participants should expect it.
2. As a chat owner, I want to disable automatic memory capture for a chat, so that future background scans stop without disabling `/ai` answers.
3. As a chat owner, I want memory enablement to be independent from AI access, so that I can control capture and invocation as separate policies.
4. As an authorized user, I want `/ai` to work outside memory-enabled chats, so that memory consent does not become an AI allowed-chat gate.
5. As a chat participant, I want a standalone human message to be eligible memory, so that useful evidence is not ignored merely because nobody replied to it.
6. As a chat participant, I want replies to be retained with their bounded ancestors, so that pronouns, quotations, disagreement, and conversational context can be interpreted correctly.
7. As a chat participant, I want a standalone message that later receives replies to become the root of the same evolving episode, so that the conversation is not split into unrelated memories.
8. As a chat participant, I want only human-authored evidence retained, so that AI answers and control commands do not become self-reinforcing memory.
9. As a chat participant, I want attachment descriptions retained without raw files, so that the chat can remember their meaning without becoming a media archive.
10. As a chat participant, I want the system to preserve who spoke and who was described, so that a statement about another person is not attached to its author as a self-report.
11. As a chat participant, I want quoted and forwarded claims marked as such, so that repetition does not turn them into direct testimony.
12. As a chat participant, I want jokes, sarcasm, uncertainty, and negation preserved, so that the memory system does not flatten social context into false facts.
13. As a chat participant, I want event time, mention time, and ingestion time kept distinct, so that old events discussed today are not treated as new events.
14. As a chat participant, I want later corrections and changed plans to coexist with earlier evidence, so that the assistant can explain what changed instead of erasing history.
15. As an AI requester, I want the current chat's memory recalled automatically, so that I do not have to restate relevant shared history.
16. As an AI requester, I want exact `@` mentions resolved to stable platform identities, so that the correct person's relevant memory is considered even if they are not in the reply chain.
17. As an AI requester, I want plain-language references such as "Mina's brother" or a scoped nickname resolved when evidence supports it, so that natural chat language works without requiring an `@` mention.
18. As an AI requester, I want the assistant to follow a small number of relevant relationships, so that it can connect people, projects, places, objects, constraints, and outcomes.
19. As an AI requester, I want relationship discovery bounded by relevance and confidence, so that one mention does not load an entire social graph.
20. As an AI requester, I want the assistant to ask when two people or aliases remain ambiguous, so that it does not silently choose the wrong identity.
21. As an AI requester, I want scoped aliases and shared vocabulary understood inside their chat, so that names such as project codenames and established group phrases remain useful.
22. As an AI requester, I want the same alias in another chat treated independently, so that local language never causes cross-scope identity leakage.
23. As an AI requester, I want the latest explicit plan or decision distinguished from superseded versions, so that answers describe the current state.
24. As an AI requester, I want unresolved commitments and ownership recalled, so that the assistant can say what is still pending and who volunteered to do it.
25. As an AI requester, I want prior attempts and their conditions recalled, so that the group can avoid repeating a failed approach without treating it as universally invalid.
26. As an AI requester, I want support conversations to resume from prior evidence and pending escalation, so that people do not repeat troubleshooting history.
27. As an AI requester, I want low-stakes preferences and constraints used in recommendations, so that suggestions feel considerate and relevant.
28. As an AI requester, I want multi-step recommendations to preserve relationship direction, so that the assistant does not confuse who owns, likes, or needs something.
29. As an AI requester, I want attachment-derived group lore recalled when relevant, so that shared history can inform useful or playful answers.
30. As an AI requester, I want memory-aware humor used only when the current tone invites it, so that callbacks do not become repetitive or embarrassing.
31. As an AI requester, I want rumors and third-party claims explicitly attributed, so that the assistant does not present hearsay as verified truth.
32. As an AI requester, I want sensitive memory surfaced only when directly relevant, so that incidental personal context is not volunteered.
33. As an AI requester, I want conflicting evidence called out, so that the assistant does not manufacture certainty.
34. As an AI requester, I want source evidence available for a memory used in an answer, so that important claims can be checked against what was actually said.
35. As an AI requester, I want retained text treated as untrusted evidence rather than instructions, so that prompt text stored in memory cannot control the agent.
36. As an AI requester, I want memory failure to degrade to an ordinary AI answer, so that a memory outage does not make `/ai` unusable.
37. As an AI requester, I want a direct reply to an AI Answer to continue the existing Agent Session with the same bank boundary, so that follow-up questions remain natural and scoped.
38. As a Saved Messages user, I want private notes, links, attachment descriptions, budgets, decisions, and abandoned ideas kept in a separate bank, so that personal planning can develop without leaking into groups.
39. As an owner, I want bare `/ai_memory` on a replied message to retain the full bounded human chain in bulk, so that explicit capture is quick and quiet.
40. As an owner, I want `/ai_memory` with an instruction to revise memory using the replied evidence, so that I can correct or suppress misleading current memory without internal record IDs.
41. As an owner, I want `/ai_memory` to work as a one-shot operation in a scope where automatic capture is disabled, so that explicit remembering does not silently enable future scans.
42. As an owner, I want a forwarded source or pasted Telegram message link in Saved Messages to retain its accessible source chain, so that I can capture memory without posting a command in the original chat.
43. As an owner, I want inaccessible or privacy-protected forwarded sources to fail clearly, so that the system never guesses the original author or content.
44. As an owner, I want visible processing, success, duplicate, and failure status for Saved Messages capture, so that I know whether evidence was retained.
45. As an owner, I want successful no-instruction memory and access commands removed after execution, so that administrative commands do not add noise to the chat.
46. As an owner, I want a scheduled Dream Cycle to scan each enabled Telegram chat over a configured time window, so that ordinary conversation is learned without manual commands.
47. As an owner, I want Dream Cycles to process standalone messages and reply threads, so that reply structure improves context but never controls eligibility.
48. As an owner, I want Dream Cycles to resume from a successful per-scope cursor with bounded overlap, so that restarts and temporary failures do not lose messages.
49. As an owner, I want a settlement delay before scanning recent messages, so that active conversations and late edits are less likely to be ingested in unstable form.
50. As an owner, I want only one Dream Cycle for a scope active at a time, so that concurrent schedules do not duplicate work or overload Telegram.
51. As an owner, I want Dream ingestion to respect Telegram and memory-service rate limits, so that background capture does not trigger provider flood limits.
52. As an owner, I want failed documents retried without advancing the successful cursor past them, so that partial failures remain recoverable.
53. As an owner, I want to inspect whether a scope is enabled and when its last Dream Cycle succeeded, so that capture state is operationally visible.
54. As an owner, I want a read-only memory dashboard organized by chat bank, so that I can inspect what the system remembers without exposing write controls.
55. As an owner, I want canonical scope and actor keys shown with current display names, so that inspection is readable while identities remain stable.
56. As an owner, I want to drill from a memory or observation to its source episode, so that I can understand why it exists.
57. As an owner, I want temporal and attribution information visible in inspection, so that stale, conflicting, or third-party claims are recognizable.
58. As an operator, I want health, queue acceptance, scan cursor, ingestion receipt, and failure logging, so that I can diagnose missing or delayed memory.
59. As an operator, I want idempotent retries across `/ai`, `/ai_memory`, Dream overlap, and container restarts, so that recovery does not create duplicate memory.
60. As an operator, I want local persistent storage and restart-safe state in Docker Compose, so that the memory service survives normal container recreation.
61. As an operator, I want the existing OpenAI-compatible chat endpoint and local Qwen embedding service reused, so that the new memory system does not add another paid model dependency.
62. As an operator, I want the embedding model and dimension pinned for each bank, so that stored and query vectors remain in one semantic space.
63. As an operator, I want Zvec source evidence re-ingested and re-embedded before cutover, so that useful existing observations survive without copying incompatible vectors.
64. As a memory integration developer, I want the memory engine to remain client-neutral, so that Telegram is one producer rather than part of the memory domain.
65. As a memory integration developer, I want source event IDs to be optional opaque provenance, so that clients without Telegram-style message IDs can still retain episodes.
66. As a memory integration developer, I want stable document identity available independently from source message IDs, so that clients can choose idempotent updates or immutable one-shot episodes.
67. As a memory integration developer, I want ordered events to carry stable actors, display labels, content, timestamps, references, and metadata, so that another chat system can serialize equivalent evidence.
68. As an agent developer, I want bank-pinned read-only memory tools with fixed budgets, so that the model can reason over memory without gaining storage administration capabilities.
69. As an agent developer, I want Runtime Inference excluded from automatic retention, so that one model guess does not become evidence for future model guesses.
70. As a project maintainer, I want one selected memory engine and no fallback runtime, so that behavior, operations, and tests do not split across competing implementations.

## Implementation Decisions

- Hindsight v0.8.4 or a later explicitly reviewed compatible release is the sole runtime memory engine. The existing Zvec memory core, subject profiles, custom fact extraction, and hybrid retrieval are retired after migration. There is no dual-write period, fallback backend, custom assertion graph, or alias table.
- Hindsight remains a standalone service with its native HTTP API, storage, and UI. Telefire owns a thin client adapter and normalized episode serialization but does not publish a second general-purpose memory engine API merely to rename Hindsight operations.
- One Memory Scope maps to one Hindsight bank. The canonical scope key is the stable bank identity; a current human-readable scope label is metadata. Private chats, groups, Saved Messages, workspaces, and future clients use distinct banks.
- Trusted application code selects and pins the bank before an Agent Run. Neither prompt text nor model tool arguments can supply, enumerate, or broaden a bank identity. Hindsight tags may organize records inside a bank but are not an authorization boundary.
- Memory enablement is a persisted, owner-controlled property of a scope. It controls automatic `/ai` capture and Dream scanning only. It does not control recall, one-shot `/ai_memory`, or permission to invoke `/ai`.
- The deprecated AI allowed-chat configuration is removed. Per-user owner/whitelist authorization and delegated rate limits remain the invocation policy.
- Owner-only memory control operations enable or disable the current scope, report its state, and permit a manual Dream Cycle. Their command names use the existing `/ai_memory_*` namespace, and successful control messages follow the existing low-noise deletion behavior.
- The normalized ingestion unit is an Episode containing ordered source events. Each event can carry an optional opaque source event ID, stable actor key, display name, occurred time, mention time, text, generated attachment description, reply or quotation references, exact mentioned actor keys, and bounded integration metadata.
- Source event IDs are provenance, not the portable identity contract. A client may provide an independent stable document identity for update semantics. For immutable one-shot episodes without a stable source identity, the adapter derives a deterministic content fingerprint and returns the resulting document identity.
- Telefire derives a stable document identity from the canonical scope and bounded reply root. A standalone message uses itself as the root. A thread that grows later continues to update the same document.
- An exact content version is a no-op. Any new reply, edit, or explicit replay that changes a structured Episode replaces the complete bounded Hindsight document and permits re-extraction. Full replacement is required for idempotent crash recovery because the pinned Hindsight release cannot structurally append top-level Episode objects.
- Telefire stores delivery state only: memory-enabled scopes, stable document mappings, content versions, ingestion receipts, successful Dream cursors, and operational timestamps. This state must not duplicate Hindsight facts, entities, relationships, observations, or relevance scores.
- All three capture paths call the same episode serializer and retention client. They may batch several document updates in one transport request, but batching does not merge unrelated documents into one episode.
- Successful `/ai` capture occurs after the answer has completed and only in a Memory-Enabled Scope. It retains the bounded human reply thread plus the current human prompt, excludes stored AI Answers and control commands, and does not hold a delegated user's request-rate lease while memory work finishes.
- Bare owner `/ai_memory` retains the bounded human reply chain, Saved Messages source, or resolved message-link source even if the source scope is not memory-enabled. An instructed `/ai_memory` first retains the evidence, then applies an evidence-backed Revision to the directly targeted human Memory Subject.
- A Revision is represented through Hindsight-native evidence, temporal update, directive, or document correction capabilities selected during the Hindsight integration spike. It must suppress or supersede misleading current recall while preserving source history. Telefire must not recreate the old Markdown Subject Profile or a parallel suppression index. No hard-delete operation is exposed in this version.
- The Telegram Dream Cycle runs on a configurable cron schedule inside the Telegram integration, not inside Hindsight and not inside the answer agent. It scans every enabled Telegram scope over a configured window with bounded concurrency, overlap, and settlement delay.
- For each scanned message, Dream finds the bounded reply root. Non-replies form one-message documents. Replies update their root document. Ancestors just outside the scan window may be fetched as context but are not counted as newly observed events.
- A Dream scan watermark advances only through a fixed time interval whose documents were all accepted for retention. Failed, oversized, or incomplete windows remain retryable, and a later cycle cannot silently skip past them. A renewable per-scope lease prevents overlapping cycles.
- Telegram flood waits and Hindsight backpressure are handled with bounded client-side rate limiting and retry delay. Background retention never edits Telegram messages repeatedly to report progress.
- Raw photos, files, voice notes, and Telegram download URLs remain transient. Only bounded generated descriptions or extracted text and safe metadata enter an Episode.
- Every AI Request makes one low- or mid-budget bank-scoped recall before the Agent Run. Its query combines the main prompt, bounded reply context, deterministic participants, and exact platform mentions. Recall returns both recent facts and consolidated observations because Hindsight observations may lag new evidence.
- Exact Telegram mentions are resolved by trusted Telegram APIs to canonical actor keys and supplied as explicit entity hints. Plain names, aliases, kinship, roles, projects, places, objects, and indirect references are resolved by Hindsight within the pinned bank rather than by Telefire-specific alias rules.
- Pi receives read-only, bank-pinned operations equivalent to memory recall, one low- or mid-budget reflect, and bounded source retrieval. The host fixes call count, retrieval depth, token budget, result limits, and bank. Tools do not expose retain, revise, delete, list-bank, create-bank, or arbitrary source enumeration operations.
- One reflect call is available only when implicit identity, temporal reconciliation, ambiguity, or a relevant multi-step relationship cannot be handled by ordinary recall. The answer must stop or ask for clarification when evidence remains weak. Telefire does not implement manual recursive entity expansion.
- Memory Context is labeled untrusted background evidence in the Agent Run. Stored text cannot override the system policy, current prompt, tool policy, or scope guard.
- Source evidence is fetched only for memories selected for use. The answer preserves attribution, relevant timestamps, uncertainty, and conflicts. Runtime Inference may shape the answer but is not retained automatically.
- Low-stakes preferences, skills, plans, decisions, commitments, outcomes, shared achievements, established group language, and group lore may surface naturally when relevant. Rumors and third-party claims remain attributed. Sensitive content is reactive-only. Insults, harassment, speculative relationships, one-off sarcasm, inferred personality traits, and embedded instructions are not presented as durable truth.
- Actor identity uses an integration-owned canonical key plus a current display label. Telefire deterministically encodes canonical actor hints into Hindsight's entity input and validates during the integration gate that extraction and deduplication preserve identity across aliases and renamed accounts. It does not automatically merge identities across banks or across platform accounts.
- The read-only inspection experience is bank-first rather than subject-profile-first. It shows canonical bank and actor keys beside display names and permits navigation among episodes, memories, entities, observations, time, and source evidence. It also shows Telefire-owned enablement and Dream delivery status without pretending those receipts are memory facts.
- The current subject-centric dashboard and its Zvec read model are retired. Hindsight's native inspection UI is used where it exposes the required evidence, with a thin operational status view only for Telefire-owned enablement, receipts, and Dream cursors.
- Hindsight reuses the configured OpenAI-compatible chat model endpoint and the local `qwen3-embedding:0.6b` OpenAI-compatible embedding endpoint. The embedding model and dimension are pinned for a bank's lifetime. A model change requires explicit re-ingestion and re-embedding.
- The local Compose stack adds Hindsight with persistent storage and keeps it reachable to Telefire over the internal network. The dashboard is published only on an explicitly configured local interface and retains private response headers or equivalent access controls. Embedded storage is sufficient for this version; external PostgreSQL and high availability are deferred.
- Migration exports retained Zvec Observations and identity labels, converts each recoverable source observation into a standalone legacy Episode, re-ingests it through Hindsight, and re-embeds it. Derived Zvec facts, episodes, vectors, and automatic profiles are not copied as source truth. Explicit owner Revisions are preserved as marked legacy correction evidence where recoverable.
- Cutover requires a successful migration report and the behavioral gate. After cutover, the old Zvec volume is retained as an offline rollback artifact for a bounded period but is never queried by the running application.

## Testing Decisions

- Tests assert external behavior and durable contracts: which evidence is retained, which bank is queried, what tools are available, what answer context is produced, how retries converge, and what a user sees. They do not assert Hindsight's private database schema, exact extracted wording, internal graph shape, or model call sequence beyond the bounded public operations.
- The primary high-level seam is the existing Telegram-facing AI conversation handler. Tests use fake Telegram message objects and a fake Agent Engine while exercising Telefire's real episode serializer, delivery state, and HTTP memory client against a controlled Hindsight HTTP double. This proves `/ai`, follow-ups, `/ai_memory`, Saved Messages, explicit mentions, prompt assembly, capture policy, and fail-open behavior through one user-facing seam.
- The same high-level seam covers standalone messages, reply-root grouping, later replies, edited content versions, AI-output exclusion, attachment descriptions, enabled and disabled scopes, command deletion, and delegated users without requiring live credentials.
- Focused HTTP contract tests validate Telefire against the public Hindsight retain, recall, reflect, source, bank, and health responses. Malformed responses, timeouts, queue rejection, observation lag, and server errors must be covered without depending on Hindsight internals.
- Focused Dream tests use a fake Telegram history source and a controlled memory endpoint. They prove standalone eligibility, reply grouping, ancestors outside the window, overlap, settlement delay, cursor advancement, partial failure retry, per-scope leases, batching, restart recovery, and bounded rate limiting.
- Focused identity tests prove exact platform mentions become canonical entity hints, display-name changes keep the same canonical actor, identical aliases remain isolated by bank, and ambiguous same-bank names do not silently merge.
- Focused security tests prove the agent cannot select or enumerate banks, memory text cannot invoke tools, delegated and owner runs receive the same bank boundary, source retrieval is bounded, write/delete tools are absent, and no other bank's evidence enters prompts or traces.
- Migration tests run against a temporary real Zvec source and real Hindsight container. They prove only source observations, labels, and recoverable explicit revisions migrate; vectors and derived facts do not; repeated migration is idempotent; and counts plus rejected records appear in a report.
- The Hindsight behavioral release gate uses seven fixed scenario families: scoped alias plus direct constraint; implicit identity plus two-step relation; three-step person-to-person-to-object relation; changing plan, promise, and completion; hearsay, direct verification, retraction, and temporal precedence; attachment-derived group lore; and ambiguous same-name users plus identical aliases in separate banks.
- Each behavioral scenario is queried with three materially different paraphrases. A pass requires the necessary evidence in the recall or reflect trace, no evidence from another bank, preserved attribution and time, no invented relationship, calibrated uncertainty, and equivalent conclusions across paraphrases. Exact prose is not compared.
- Real Hindsight integration tests run in Docker with the configured OpenAI-compatible chat endpoint and local Qwen embedding service. They validate canonical entity encoding, replacement and no-op behavior, background observation lag, source retrieval, pinned-bank isolation, and restart persistence.
- A live Telegram smoke test uses the existing owner, second user session, and disposable test group. It proves opt-in scope control, one standalone Dream capture, one reply-thread capture, one explicit `@` reference, one implicit reference, one `/ai_memory` source-link capture, a continuation, and absence of cross-chat recall. It cleans up only messages it created.
- Operational recovery testing restarts the AI and memory containers during retain and recall, then verifies accepted work, cursor recovery, idempotent retries, dashboard availability, and no partial cross-bank records.
- Existing tests for AI conversations, memory integration, Saved Messages, access control, attachments, streaming, the HTTP memory client, and the current memory service provide prior art. They should be migrated or replaced at the same behavioral seam rather than preserved through a compatibility adapter for the obsolete Zvec contract.
- Success requires the focused suite, full project suite, Hindsight behavioral gate, Compose health checks, migration dry run, and live Telegram smoke test to pass. Any check not run must be reported explicitly with its remaining risk.

## Out of Scope

- Automatic recall across banks, chats, workspaces, Saved Messages, or platform accounts.
- A global person directory or automatic cross-scope identity merge.
- A Telefire-owned assertion graph, alias table, relationship database, Subject Profile, vector index, or memory fallback.
- Graphiti, Mem0, Zvec, TencentDB Agent Memory, Nowledge Mem, or another runtime memory backend alongside Hindsight.
- Unbounded recursive retrieval, exact caller-controlled graph hop counts, arbitrary agent-selected recall budgets, or model-selected banks.
- Memory write, revision, deletion, scope administration, or Dream control tools exposed to the answer agent.
- Automatic persistence of Runtime Inference, recommendations, personality judgments, inferred relationships, or agent-generated answers.
- Raw attachment, image, voice, video, PDF, or Telegram URL storage in memory.
- Hard deletion or full legal erasure workflows in the first version. Revision and suppression preserve provenance.
- Automatic synchronization of Telegram message deletion into retained memory.
- Full historical backfill of every enabled chat. Initial capture uses an explicit bounded window; older history requires an intentional import operation.
- Dream scanners for Matrix or other chat systems in the first implementation. The memory engine and Episode model remain client-neutral so those adapters can be added later.
- Dashboard editing, graph manipulation, or record-by-record moderation. Inspection is read-only; explicit Revision remains the correction path.
- Remote multi-tenant Hindsight hosting, external PostgreSQL, high availability, disaster-recovery automation, or public internet exposure.
- Multiple embedding spaces within one bank or copying existing Zvec vectors into Hindsight.
- Proactive unsolicited AI messages based on memory. Memory augments an explicit AI Request only.
- General document knowledge-base ingestion, community summarization, or GraphRAG over arbitrary corpora.

## Further Notes

- ADR-0005 is authoritative when this specification and older Zvec tickets differ. The old subject-scoped tickets describe shipped behavior and migration input, not the target memory architecture.
- The desired product feeling is evidence-backed continuity: it remembers how it learned something, understands that knowledge can change, relates a small number of relevant entities, and says when a connection is possible rather than established.
- The most important integration risk is Hindsight's explicit entity contract: it accepts names and types but does not promise a separate external canonical ID. Canonical identity encoding must be proven by the behavioral gate before migrated data is cut over.
- Consolidated Hindsight Observations can lag newly retained facts. Prompt assembly must include recent facts as well as observations and must not interpret temporary observation lag as missing ingestion.
- The chosen highest test seam is the Telegram-facing AI handler with real Telefire delivery and memory-client behavior against a controlled Hindsight boundary. Ticket decomposition should preserve that seam and introduce narrower tests only for delivery scheduling, migration, and Hindsight contract risks.
- This local specification uses `status: ready-for-agent` as the file-based equivalent of the tracker label. Ticket decomposition should be produced from this specification in a separate `to-tickets` pass.
