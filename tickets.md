# Tickets: Hindsight contextual chat memory

These tickets replace the subject-scoped Zvec memory runtime with Hindsight and add scoped conversational capture, bounded memory reasoning, Dream Cycles, inspection, and migration. The source specification is [Hindsight Contextual Chat Memory](docs/specs/hindsight-contextual-chat-memory.md), governed by ADR-0005.

Work the **frontier**: any ticket whose blockers are all done. The initial frontier is **Validate and Pin the Hindsight Contract**.

## Validate and Pin the Hindsight Contract

**What to build:** Prove the exact Hindsight behavior Telefire depends on using a real locally deployed service, then pin that tested contract before changing Telegram memory behavior.

**Blocked by:** None - can start immediately.

- [x] The local Docker Compose stack starts a pinned Hindsight release with persistent storage, a health endpoint, and its inspection UI.
- [x] Hindsight uses the configured OpenAI-compatible chat endpoint and the existing local `qwen3-embedding:0.6b` embedding endpoint without introducing another paid model dependency.
- [x] One fixture retains an ordered multi-speaker conversation with stable actor hints, display names, timestamps, replies, quotations, explicit mentions, and caller metadata.
- [x] Two different Memory Scopes map to isolated banks, and recall, reflect, source retrieval, and UI inspection never expose evidence from the other bank.
- [x] Exact retain retries are no-ops, while new replies and edited earlier events idempotently replace and re-extract the complete bounded document.
- [x] Canonical actor hints survive extraction well enough to distinguish aliases, renamed display labels, and two same-name actors in one bank.
- [x] Recall returns recent facts and consolidated observations with usable temporal, entity, document, chunk, and source provenance.
- [x] One low- or mid-budget reflect operation can answer the representative two-step and three-step relationship fixtures with evidence references.
- [x] A Hindsight-native mechanism is selected and documented for evidence-backed correction, supersession, and suppression without a parallel Subject Profile or hard deletion.
- [x] The supported retain, recall, reflect, source, bank, health, and correction response shapes are covered by automated real-service contract tests.
- [x] Any failed contract requirement blocks the dependent tickets and is reported directly; no fallback memory engine or Telefire-owned graph is introduced.

## Remember and Recall One Telegram Conversation

**What to build:** Let the owner retain one bounded human reply chain with bare `/ai_memory`, then use that evidence in a later `/ai` answer from the same chat bank.

**Blocked by:** Validate and Pin the Hindsight Contract.

- [x] Trusted Telefire code maps the active Telegram chat to exactly one Hindsight bank before memory or agent work begins.
- [x] Bare owner `/ai_memory` serializes the bounded human reply chain as one ordered Episode rather than one subject-owned request per message.
- [x] Episode events preserve stable actors, current display labels, event time, mention time when available, reply and quotation structure, explicit mentions, attachment descriptions, and bounded provenance metadata.
- [x] Optional Telegram message IDs remain opaque source provenance; stable document identity is derived separately from the scope and reply root.
- [x] Stored AI Answers, AI control commands, and unsupported non-human messages are excluded from retained evidence.
- [x] A successful bare command receives a concise stored or already-remembered acknowledgement and follows the existing owner command-deletion behavior.
- [x] Repeating the same command or retrying after a response failure does not duplicate the Hindsight document or derived memory.
- [x] A later `/ai` request performs one bounded recall from the pinned bank using its main prompt, reply context, deterministic participants, and explicit references.
- [x] Recalled content is labeled as untrusted Memory Context and cannot override system policy, the current prompt, or tool policy.
- [x] Memory timeout, malformed response, or unavailability is logged and degrades to an ordinary AI answer.
- [x] The Telegram-facing handler tests exercise the real episode serializer, delivery state, and HTTP client against a controlled Hindsight boundary.
- [x] This flow no longer calls the subject-scoped Zvec ingest, augment, or Subject Profile behavior; no compatibility adapter renames those obsolete operations.

## Capture Saved Messages Sources as Episodes

**What to build:** Let the owner retain an accessible Telegram source by forwarding it or pasting its message link in Saved Messages, using the same Hindsight Episode pipeline as `/ai_memory`.

**Blocked by:** Remember and Recall One Telegram Conversation.

- [x] A newly forwarded Saved Messages item with an accessible source pointer resolves the original message and its bounded human ancestors.
- [x] A supported private or public Telegram message link pasted in Saved Messages resolves the same source and bounded chain.
- [x] The resolved chain is serialized once through the common Episode pipeline and retained in the original source scope's Hindsight bank.
- [x] Saved Messages capture remains a one-shot write and does not enable automatic capture for the source scope.
- [x] A hidden, deleted, inaccessible, or privacy-protected source fails without guessing content, attribution, identity, or scope.
- [x] The forwarded or pasted source body remains in Saved Messages and is not destructively rewritten.
- [x] Processing, success, duplicate, retryable failure, and inaccessible-source states are visible without frequent Telegram message edits.
- [x] A durable receipt prevents duplicate processing of one Saved Messages item while allowing an intentional re-forward or re-paste as a new attempt.
- [x] Attachment descriptions and extracted text are retained when available, while raw bytes, Telegram URLs, and temporary paths remain transient.
- [x] Automated tests cover forwards, private links, public links, duplicate delivery, hidden sources, failures, status rate limiting, and restart persistence.

## Enable Scope Memory and Learn After `/ai`

**What to build:** Let the owner opt a chat into automatic memory and have successful AI Requests retain their bounded human episode without changing who may invoke `/ai`.

**Blocked by:** Remember and Recall One Telegram Conversation.

- [x] Owner-only `/ai_memory_enable`, `/ai_memory_disable`, and `/ai_memory_status` operations manage the current scope's persisted Memory-Enabled state.
- [x] Successful enable and disable command messages follow the existing low-noise deletion behavior, while acknowledgements and status remain readable.
- [x] Non-owner attempts do not change scope state or expose memory administration details.
- [x] A successful `/ai` request in a Memory-Enabled Scope retains the bounded human reply thread and the current human prompt through the common Episode pipeline.
- [x] A failed or cancelled Agent Run does not trigger automatic episode retention.
- [x] An AI Request in a disabled scope can still recall existing one-shot memory but does not automatically write new evidence.
- [x] Automatic retention happens after the AI Answer path and does not hold a delegated user's request-rate lease.
- [x] AI Answers, tool snapshots, control commands, and sibling reply branches are excluded from the retained Episode.
- [x] Reprocessing the same AI Conversation converges through document identity and content versioning instead of creating duplicate memory.
- [x] Per-user owner and whitelist authorization remains the only AI invocation policy.
- [x] The deprecated AI allowed-chat setting and gate are removed from runtime configuration, Compose, tests, live-test setup, and operational documentation.
- [x] Tests cover persistent enablement, disablement, owner checks, successful and failed runs, disabled-scope recall, post-answer retention, and removal of the chat gate.

## Capture Ordinary Chat with a Manual Dream Cycle

**What to build:** Let the owner manually scan one enabled Telegram scope so standalone messages and reply threads become memory without requiring `/ai` or `/ai_memory` on each message.

**Blocked by:** Enable Scope Memory and Learn After `/ai`.

- [x] Owner-only `/ai_memory_dream` scans a configured bounded time window for the current Memory-Enabled Scope.
- [x] Every eligible standalone human message becomes a one-message document with itself as the stable reply root.
- [x] Replies in the scan window are grouped under their bounded root and retained as ordered document updates with conversational context.
- [x] A standalone root retained in an earlier cycle accepts later replies through complete-document replacement rather than creating a second unrelated document.
- [x] An edited previously retained event changes the content version and uses the validated bounded replace behavior.
- [x] Ancestors just outside the scan window may be fetched for context but are not counted or delivered as newly observed events.
- [x] Stored AI Answers, tool snapshots, bots, control commands, and unsupported content are excluded from eligible human evidence.
- [x] Existing attachment description and text-extraction behavior is reused, and no raw media persists in memory or delivery state.
- [x] Stable document mappings, content versions, and ingestion receipts make manual retries and overlap idempotent.
- [x] The successful cursor advances only through document updates accepted for retention; failed documents remain retryable.
- [x] The command produces one bounded completion summary instead of editing progress messages for each document.
- [x] Tests cover standalone messages, reply grouping, late replies, edits, outside-window ancestors, exclusions, attachments, partial failure, cursor behavior, and restart-safe receipts.

## Run Scheduled and Recoverable Dream Cycles

**What to build:** Run Dream Cycles automatically for every enabled Telegram scope with bounded scheduling, retries, rate limiting, and restart recovery.

**Blocked by:** Capture Ordinary Chat with a Manual Dream Cycle.

- [x] A configurable cron schedule triggers Dream work in the Telegram integration rather than in Hindsight or the answer agent.
- [x] Each cycle scans all currently enabled Telegram scopes using configured lookback, overlap, settlement delay, concurrency, and batch limits.
- [x] A settlement delay prevents actively changing recent messages from being processed immediately.
- [x] Bounded overlap catches late replies, edits, delayed delivery, and work whose previous acknowledgement was lost.
- [x] A per-scope lease prevents manual and scheduled cycles from processing the same scope concurrently.
- [x] Multiple document updates may share one retain transport batch without merging unrelated Episodes.
- [x] Telegram flood waits and Hindsight backpressure use bounded client-side delay and retry rather than tight loops or repeated message edits.
- [x] A failed document does not disappear behind an advanced successful cursor, and healthy documents remain idempotent when retried.
- [x] Scheduler, cursor, receipts, and leases survive AI container restart without duplicating accepted evidence.
- [x] Automatic scanning stops after a scope is disabled, while already accepted Hindsight memory remains recallable.
- [x] Scope status exposes the last attempted and successful Dream times plus a bounded failure summary.
- [x] Tests use a fake Telegram history source and controlled Hindsight endpoint to cover multi-scope scheduling, overlap, settlement, leases, batching, flood waits, partial failure, disablement, and restart recovery.

## Resolve Exact Mentions to Stable Actors

**What to build:** Resolve deterministic Telegram participants and exact `@` mentions to stable actors so relevant memory is considered even when the mentioned person is not in the reply chain.

**Blocked by:** Remember and Recall One Telegram Conversation.

- [x] Episode serialization supplies every deterministic speaker and exact mentioned Telegram user as an explicit Hindsight entity hint with a stable canonical key; Episode actor metadata carries the current display label.
- [x] Trusted Telegram resolution, not model inference, determines the canonical actor behind an exact `@` mention.
- [x] The recall query includes exact mentioned actors even when they are absent from the bounded reply chain.
- [x] Changing a Telegram display name preserves the canonical actor and updates the readable label without rewriting historical evidence.
- [x] An alias learned from evidence can resolve to the canonical actor later inside the same bank.
- [x] Two actors with the same display name in one bank remain distinct and cause clarification when the current evidence is ambiguous.
- [x] The same nickname or alias in two banks never creates a cross-bank merge or recall.
- [x] Plain text that resembles an `@` mention but does not resolve through Telegram is treated as untrusted content rather than a fabricated identity.
- [x] The dashboard and source evidence show readable labels alongside canonical actor keys.
- [x] Tests cover an out-of-chain exact mention, renamed user, scoped alias, two same-name actors, invalid mention, and identical aliases in isolated banks.

## Reason Over Implicit Identities and Relationships

**What to build:** Let the AI agent use bounded, bank-pinned memory reasoning to resolve implicit people and follow a small number of relevant relationships with source evidence.

**Blocked by:** Resolve Exact Mentions to Stable Actors.

- [x] Every AI Request retains its single host-controlled initial recall from the pinned bank.
- [x] Pi can access the bounded recall result, at most one low- or mid-budget reflect operation, and source retrieval for memories selected for use.
- [x] Memory tool schemas contain no bank identity, write action, delete action, arbitrary result limit, token budget, or call-count override.
- [x] Owner and delegated Agent Runs receive the same bank isolation and read-only memory boundaries.
- [x] Reflect is used only for unresolved identity, temporal reconciliation, ambiguity, or a relevant multi-step relationship that ordinary recall did not settle.
- [x] Source retrieval accepts only memory references already returned within the active bank and enforces fixed item and character limits.
- [x] Retained text remains untrusted evidence and cannot invoke tools, expand the bank, alter host budgets, or become system instructions.
- [x] Runtime Inference can shape an answer but is never automatically retained as a Fact, Episode, Observation, or Revision.
- [x] Weak, conflicting, or ambiguous evidence produces calibrated language or a clarification request instead of an invented identity or relationship.
- [x] A reflect or source failure falls back to bounded recall context and does not prevent an otherwise valid AI Answer.
- [x] Agent traces identify which recalled memories and source evidence supported the answer without exposing secrets or another bank.
- [x] Automated scenarios cover an implicit sibling, a two-step constraint recommendation, a three-step person-to-object relation, changing plans, conflict, ambiguity, prompt injection, and strict bank isolation.

## Revise Memory from Replied Evidence

**What to build:** Let the owner use instructed `/ai_memory` to correct or suppress current memory for the directly replied human while preserving the original evidence and history.

**Blocked by:** Remember and Recall One Telegram Conversation.

- [x] Only the owner can request a Revision, and the target must be the directly replied human Memory Subject.
- [x] The bounded human reply chain is retained idempotently before the Revision is applied, even when automatic capture is disabled for the scope.
- [x] The instruction and retained evidence use the Hindsight-native correction mechanism validated by the contract ticket.
- [x] A later correction can supersede an earlier current state while both dated evidence trails remain inspectable.
- [x] A suppression or forget instruction prevents matching content from normal future recall without hard-deleting its source Episode.
- [x] A Revision never recreates the old Markdown Subject Profile, subject-owned Fact model, or a Telefire suppression index.
- [x] A target AI Answer, missing reply, ambiguous target, malformed instruction, or memory-service failure produces a bounded owner-visible error and no guessed target.
- [x] A successful Revision produces a concise acknowledgement without exposing internal Hindsight identifiers or raw model output.
- [x] Revision effects remain isolated to the active bank and do not alter another chat containing the same canonical actor.
- [x] Tests cover addition, correction, temporal supersession, suppression, malformed output, direct-human targeting, retained evidence, history inspection, and bank isolation.

## Inspect Memory and Capture Health by Bank

**What to build:** Give the owner a read-only bank-first inspection experience for Hindsight memory and Telefire's capture-delivery state.

**Blocked by:** Run Scheduled and Recoverable Dream Cycles; Reason Over Implicit Identities and Relationships.

- [x] The primary memory view lists Hindsight banks with canonical scope keys and current chat or workspace display labels.
- [x] A bank detail view exposes bounded Episodes, extracted memories, entities, observations, temporal information, and source evidence available through Hindsight.
- [x] Actor references show current display labels alongside canonical actor keys without treating labels as identity.
- [x] A memory or observation can be traced to its supporting source document or chunk and the relevant attribution and timestamps.
- [x] The inspection experience distinguishes direct statements, third-party claims, conflicting evidence, current observations, and superseded material when Hindsight exposes that metadata.
- [x] Telefire's thin operational view shows Memory-Enabled state, stable document delivery status, latest Dream attempt and success, cursor position, receipt counts, and bounded failures.
- [x] Delivery receipts, cursors, and enablement are clearly separated from Hindsight Facts, entities, relationships, and observations.
- [x] The old subject-profile dashboard and Zvec record read model are not retained as a parallel memory representation.
- [x] The dashboard remains read-only; record editing, graph manipulation, hard deletion, and Dream administration are not exposed through it.
- [x] Untrusted display names and source text are rendered through safe text APIs, and private cache, framing, content-type, and referrer protections remain enabled.
- [x] The published endpoint binds only to the explicitly configured local interface and is not exposed publicly by default.
- [x] HTTP and browser tests cover empty, loading, populated, ambiguous-name, source-drilldown, delivery-failure, security-header, responsive, and restart states.

## Migrate Zvec Evidence and Retire the Old Runtime

**What to build:** Re-ingest recoverable source evidence into Hindsight, report the migration, cut the running stack over completely, and remove the obsolete Zvec memory implementation.

**Blocked by:** Capture Saved Messages Sources as Episodes; Run Scheduled and Recoverable Dream Cycles; Reason Over Implicit Identities and Relationships; Revise Memory from Replied Evidence; Inspect Memory and Capture Health by Bank.

- [x] A dry-run inventories legacy Observations, identity labels, explicit owner Revisions, derived records, vector metadata, malformed records, and destination banks without writing Hindsight.
- [x] Each recoverable legacy Observation becomes a marked standalone legacy Episode with stable document identity, original subject actor, scope, time, text, and available metadata.
- [x] Recoverable identity labels become bank or actor display metadata without creating synthetic factual evidence.
- [x] Recoverable explicit owner Revisions are retained as marked legacy correction evidence using the validated Hindsight mechanism.
- [x] Derived Zvec Facts, Episodes, vectors, automatic Subject Profiles, relevance scores, and suppressed copies are not imported as source truth.
- [x] Migration reuses the pinned local embedding model and re-embeds source text rather than copying an incompatible vector space.
- [x] Repeating the migration is idempotent and does not duplicate documents, memories, labels, or corrections already accepted by Hindsight.
- [x] The migration report includes examined, accepted, unchanged, skipped, and failed counts grouped by scope, with bounded actionable reasons and no secrets.
- [x] A failed scope can be retried without rolling back successfully accepted, isolated banks.
- [x] Cutover removes Zvec from the running Compose stack and makes Hindsight the only runtime memory service without dual-write or fallback reads.
- [x] Obsolete subject-scoped APIs, extraction policy, Subject Profile code, Zvec dependencies, configuration, dashboard assumptions, and compatibility tests are removed in the same controlled migration.
- [x] The pre-cutover Zvec volume is preserved as an offline rollback artifact for a documented bounded period and is never mounted or queried by the running application.
- [x] Full automated tests, a migration dry run, a real temporary migration, and post-cutover Compose health checks pass before the old runtime is considered retired.

## Pass the Contextual Memory Release Gate

**What to build:** Verify the completed Hindsight system against the agreed behavioral, security, migration, recovery, and live Telegram scenarios before treating it as deployed.

**Blocked by:** Migrate Zvec Evidence and Retire the Old Runtime.

- [x] The scoped alias plus directly stated constraint scenario passes with three materially different query paraphrases.
- [x] The implicit person plus two-step recommendation scenario passes with three materially different query paraphrases.
- [x] The three-step person-to-person-to-object relationship scenario passes with three materially different query paraphrases.
- [x] The superseded plan, promise, ownership, and completion scenario passes with three materially different query paraphrases.
- [x] The hearsay, direct verification, retraction, and temporal precedence scenario passes with three materially different query paraphrases.
- [x] The attachment-derived group-lore scenario passes with three materially different query paraphrases and no raw media persistence.
- [x] The ambiguous same-name and identical cross-bank alias scenario passes with three materially different query paraphrases.
- [x] Every behavioral pass includes the necessary evidence in recall or reflect traces, preserves attribution and time, avoids invented relationships, uses calibrated uncertainty, and reaches equivalent conclusions without exact-prose comparison.
- [x] No scenario includes evidence, entities, source chunks, or operational state from another Hindsight bank.
- [x] Prompt-injection fixtures prove retained text cannot select banks, invoke memory writes or deletion, raise budgets, or escape read-only tool policy.
- [x] Container restart during retain, recall, and Dream work recovers accepted work, cursors, receipts, leases, and dashboard access without duplicate or partial cross-bank records.
- [x] The migration dry run and real temporary migration produce complete reports and preserve the old Zvec source as an offline artifact.
- [x] The local Compose stack starts healthy with AI, Pi, Hindsight, and the embedding service, and remains healthy through recreation.
- [x] A live Telegram smoke test uses the existing owner, second authorized session, and disposable group to prove enablement, standalone Dream capture, reply capture, exact mention, implicit reference, Saved Messages source capture, continuation, and cross-chat isolation.
- [x] Live tests send only synthetic content to the explicit disposable group and clean up only messages they created.
- [x] Operational documentation describes configuration, scope controls, Dream scheduling, dashboard access, migration, backup, recovery, and known first-version limits without stale Zvec or AI allowed-chat instructions.
- [x] Focused tests, the full project suite, behavioral gate, Compose checks, migration checks, and live smoke test all pass; any intentionally unrun check is reported with its exact residual risk.

## Verification Evidence (2026-07-13)

- Changed-file Ruff: 18 Python files pass `ruff check` and `ruff format --check`; `git diff --check` passes. Repository-wide Ruff still reports 100 pre-existing findings in untouched legacy plugins and is not presented as a clean gate.
- Default Python suite: `169 passed, 5 skipped`. The skipped cases are the explicit real-service and optional legacy-backend gates run separately below.
- Pi Agent Engine: `32 passed`; `npm audit --omit=dev` reports 0 vulnerabilities.
- Real Hindsight contract: `4 passed`, including the production `MemoryEpisode` serializer, retain/replace, recall, actor identity, reflection, invalidation, and bank isolation.
- Seven-family Hindsight behavioral gate: `1 passed` in 94.36 seconds; each family uses three query paraphrases.
- Migration dry run and idempotent execute examined 344 legacy documents across 6 banks: 181 unchanged source/correction documents, 161 derived records skipped, 2 profile-only records skipped, 18 recoverable suppressions, and 0 failures. The source vector metadata is `qwen3-embedding:0.6b` with dimension 1024.
- Restart drill interrupted a retain, recreated Hindsight/Pi/dashboard, recovered one stable document, and preserved recall and dashboard access. Dream lease, watermark, retry, and crash-window behavior also pass focused regression tests.
- Compose was rebuilt and force-recreated. AI, Pi, Hindsight, dashboard, and the standalone Qwen embedding service are healthy; every application container is read-only, drops all capabilities, and uses `no-new-privileges`. The offline `telefire-legacy-zvec` volume is not mounted by a runtime service.
- Browser QA passed populated bank navigation, source and memory-evidence drill-down, safe rendering, foreign-Host rejection, desktop layout, and a 390x844 viewport with no page overflow (`1151px` table inside a `366px` scroll panel).
- Live Telegram smoke passed with the existing owner and second account in the two-member disposable group: authorization, enable/disable, standalone and reply Dream capture, explicit memory, exact mention, continuation, implicit alias, delegated `code_exec`, attachment description, Saved Messages source link, revision, bank isolation, deny, and cleanup.
