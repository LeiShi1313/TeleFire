# Tickets: AI conversations and scoped memory

These tickets build reply-branch AI conversations for Telefire and a standalone Zvec-backed memory service, following the project glossary and ADR-0002/ADR-0003.

Work the **frontier**: any ticket whose blockers are all done. The initial frontier is **Stream an owner-only AI Answer** and **Remember and retrieve scoped Facts and Episodes**.

## Full-autonomy preflight

Keep all credential values outside this file and outside git. Complete this checklist before live implementation testing begins.

- [x] Python 3.14, `uv`, the locked Telefire runtime, and source compilation work locally.
- [x] Zvec 0.5.1, OpenAI 2.45.0, pytest 9.1.1, and pytest-asyncio 1.4.0 install and run on the current Python 3.14 environment.
- [x] A real temporary Zvec collection can store and retrieve string fields and vectors; caller-facing namespaced IDs will be stored as fields behind generated Zvec-safe document IDs.
- [x] Telegram is reachable and at least one non-bot user identity has an authorized local session.
- [x] The configured chat endpoint accepts authenticated non-streaming and streaming Chat Completions requests with the configured model.
- [x] Restrict the existing Telefire configuration and Telegram session files to owner-only filesystem permissions before adding more credentials.
- [x] Add an authorized session for a second, distinct, non-bot Telegram user; `ai_e2e_peer2` is the distinct peer, while `ai_e2e_peer` resolves to the owner identity.
- [x] Provide one disposable private test group containing only the owner and test user, with the owner able to administer and clean up test messages, plus its explicit numeric chat ID.
- [x] Provide an OpenAI-compatible Embeddings endpoint and model: the loopback-only standalone Ollama stack serves `qwen3-embedding:0.6b` at `http://127.0.0.1:11434/v1` with 1024-dimensional vectors.
- [x] Run non-mutating provider probes for non-streaming chat, streaming deltas, JSON extraction, batched embeddings, and embedding dimension.
- [x] Run a Telegram preflight that proves both sessions are distinct users, both can access the explicit test group, and no test can send outside that chat.
- [x] Establish tests-only pytest collection so the ignored legacy `__pypackages__` tree and command plugin named `auto_reply_test.py` are never collected.

## Stream an owner-only AI Answer

**What to build:** Let the Telefire owner start the AI command, send `/ai <prompt>`, and receive an OpenAI-compatible answer that appears progressively in one Telegram reply.

**Blocked by:** None — can start immediately.

- [x] The owner can start the AI userbot through the normal Telefire command surface and send `/ai <prompt>` without replying to another message.
- [x] Exactly one AI Answer is sent as a reply to the Trigger Message and edited at a bounded cadence as streamed text arrives.
- [x] The provider base URL, API key, chat model, output limit, and edit cadence are configurable without logging secrets.
- [x] Empty prompts and provider failures finish predictably without leaving a permanent loading message or crashing the userbot.
- [x] Automated tests cover command parsing, streamed edits, finalization, and provider-error behavior without requiring Telegram or provider credentials, and the test runner collects only the project's dedicated test suite.

## Remember and retrieve scoped Facts and Episodes

**What to build:** Let any memory client submit one subject-authored observation and retrieve relevant, strictly scoped Facts and Episodes through a standalone Python core or its HTTP service.

**Blocked by:** None — can start immediately.

- [x] `ingest` accepts one namespaced subject, one scope, one text payload, a timestamp, and optional opaque origin metadata.
- [x] The original Observation is retained under a generated identifier, while an exact retry is recognized and does not create duplicate derived memory.
- [x] A configured OpenAI-compatible model extracts validated Facts and Episodes, and a configured embedding model supplies vectors stored in Zvec.
- [x] Embedding base URL and API key may override the chat provider settings but default to them when one provider supports both APIs.
- [x] The store records its embedding model identity and vector dimension, rejects incompatible query configuration, and requires an explicit full re-embedding rebuild when either changes.
- [x] `augment` returns a bounded structured Memory Context using strict subject and scope filters, hybrid semantic and keyword relevance, and a secondary recency signal for Episodes.
- [x] Calling `augment` without a scope returns no scoped Facts or Episodes, and requesting one scope never falls back to another.
- [x] The HTTP service is the sole Zvec writer, binds to loopback by default, and has the same observable semantics as direct use of the Python core.
- [x] Integration tests use a temporary real Zvec store and a deterministic fake OpenAI-compatible server; they require no external credentials.

## Continue and fork AI Conversations

**What to build:** Let an owner start `/ai` inside an existing reply chain and continue naturally by replying to AI Answers, with each reply branch representing its own AI Conversation.

**Blocked by:** Stream an owner-only AI Answer.

- [x] A Trigger Message may stand alone or reply anywhere in a Telegram reply branch.
- [x] Only text after `/ai` is the Initial Prompt; ancestor messages are labeled untrusted Reply Context rather than instructions.
- [x] A direct owner reply to an AI Answer becomes a Follow-up Prompt without requiring `/ai`, while ordinary replies to human messages are ignored.
- [x] Replying to an earlier AI Answer forks from that point and excludes messages from sibling branches.
- [x] AI Answer markers persist locally so valid continuations still work after restarting Telefire.
- [x] Prompt assembly preserves the trusted system policy, optional labeled memory position, untrusted reference context, prior AI Conversation roles, and current instruction in the agreed order.
- [x] Context depth and size are bounded, and automated tests cover standalone, contextual, continued, forked, and restarted conversations.

## Revise and augment with Subject Profiles

**What to build:** Let a memory client deliberately update what the system knows about a subject and receive that subject's cross-scope profile alongside relevant scoped memory.

**Blocked by:** Remember and retrieve scoped Facts and Episodes.

- [x] `revise` accepts a subject, natural-language instruction, optional evidence text, and an optional scope without exposing internal memory record identifiers.
- [x] A Subject Profile is maintained as one Markdown-text Zvec record and is changed only through explicit Revision.
- [x] A validated model result can add, correct, or suppress derived memory without automatic semantic conflict replacement during normal ingestion.
- [x] Forgetting through Revision removes matching derived content from future augmentation but leaves retained Observations intact; no hard-delete API is added in v1.
- [x] `augment` includes the Subject Profile and returns scoped Facts and Episodes only when that scope was requested.
- [x] Tests cover profile creation, correction, suppression, malformed model output, and isolation between subjects and scopes.

## Delegate AI access safely

**What to build:** Let the owner grant selected Telegram users access to complete AI Conversations while keeping unauthorized and excessive use controlled.

**Blocked by:** Continue and fork AI Conversations.

- [x] The owner can reply to a person's message with `/ai_allow` or `/ai_deny`, and the whitelist persists across restarts.
- [x] Executed owner `/ai_allow` and `/ai_deny` command messages are deleted after handling while their acknowledgements remain visible.
- [x] The owner is always authorized and is not represented as a whitelist entry.
- [x] A whitelisted user can start `/ai`, continue an AI Conversation, and fork from an older AI Answer under the same prompt rules as the owner.
- [x] Requests from non-whitelisted users are ignored silently and never call the provider.
- [x] Each whitelisted non-owner user is limited to one in-flight request and a configurable cooldown, defaulting to 30 seconds; the owner is exempt.
- [x] Authorization and rate-limit tests cover initial prompts, continuations, denial, revocation, concurrency, cooldown expiry, and restart persistence.
- [x] Live Telegram tests require two explicitly named test sessions and a hard-allowlisted test chat ID, use synthetic content, and clean up messages they create.

## Augment AI Conversations and learn from them

**What to build:** Use the standalone memory service to give the requester and human reply-chain participants relevant background, then learn from the human messages that actually participate in an AI Conversation.

**Blocked by:** Continue and fork AI Conversations; Revise and augment with Subject Profiles.

- [x] Telefire identifies requesters and scopes with opaque namespaced keys and communicates with memory only through its client interface.
- [x] Before requesting an AI Answer, Telefire requests scoped Memory Context for the requester and unique human reply-chain participants, then renders the results within one shared bounded background block that cannot override system policy or the current instruction.
- [x] Memory timeout, malformed response, and unavailability are logged without preventing the AI Answer.
- [x] After the answer path, each human message used by the AI Conversation is submitted as a separate Observation attributed to its author and current chat scope.
- [x] Stored AI Answers, AI control commands, and unrelated ordinary chat traffic are never ingested; AI Answers may remain untrusted reply context but never become memory participants.
- [x] Repeated conversation processing remains idempotent through Observation retry detection.
- [x] Integration tests prove requester and participant augmentation, fair shared context budgeting, participant ingestion, strict scope handling, AI-output filtering, and fail-open behavior using isolated state that cannot modify production whitelist, conversation, or memory data.

## Revise a replied user's profile from Telegram

**What to build:** Let the owner use `/ai_memory` in a reply chain to ingest each human observation under its author, or add an instruction to revise the directly replied person's profile using that chain as evidence.

**Blocked by:** Delegate AI access safely; Augment AI Conversations and learn from them.

- [x] Only the owner can invoke `/ai_memory`, and the target subject is the sender of the replied-to message.
- [x] Bare `/ai_memory` traces the same bounded reply chain as `/ai`, skips stored AI answers and control commands, ingests each supported human message under its author, and reports whether observations were stored or already remembered.
- [x] The owner's bare `/ai_memory` command message is deleted after handling; instructed and non-owner commands are not auto-deleted.
- [x] The command text is the Revision instruction; the bounded human reply chain is ingested by author and supplied as evidence, while the Revision applies only to the directly replied human subject.
- [x] A successful Revision produces a concise acknowledgement without exposing internal record identifiers or model output.
- [x] A failed or malformed Revision produces a bounded owner-visible error; reply-chain observations remain retry-safe through ingestion deduplication.
- [x] The revised Subject Profile is available when that target later makes an authorized AI request, while other subjects remain unaffected.
- [x] End-to-end tests cover adding, correcting, and suppressing profile content from Telegram command flows.

## Show optional identity names in the memory dashboard

**What to build:** Keep canonical subject and scope keys stable while showing human-readable names resolved by client integrations such as Telegram.

**Blocked by:** Augment AI Conversations and learn from them.

- [x] The standalone memory service exposes a generic bounded identity-label upsert contract with no Telegram dependency and persists labels outside the vector records.
- [x] Identity updates create no observations, facts, episodes, embeddings, or profile changes.
- [x] Telefire resolves sender and chat names through Telegram when available and updates labels only after successful memory-producing operations.
- [x] Subject lists, details, records, search, and scope filters show display names while retaining canonical keys.
- [x] Dashboard rendering uses text-only DOM APIs for untrusted names, and the existing private response headers remain enabled.
- [x] Tests cover identity API validation, persistence, Telethon entity resolution, inspection responses, and dashboard rendering contracts.

## Render account-aware Telegram AI answers

**What to build:** Render streamed AI output with Telegram-native formatting instead of exposing model markup, while using Rich Messages when the authenticated account is a bot.

**Blocked by:** Stream an owner-only AI Answer.

- [x] User accounts receive a strict regular-message HTML response guide covering bold, italic, quotations, links, code, and preformatted compact tables.
- [x] Streamed HTML is parsed into native Telegram entities, including when opening tags arrive before visible text.
- [x] Bot accounts receive Telegram Rich Markdown instructions and edit answers through the layer-228 rich-message field, including native pipe tables.
- [x] Telethon 1.44 is required so local tools and containers understand Telegram's current message and rich-message schema.
- [x] Automated tests cover account selection, native regular-message entities, rich-message edits, and split streaming tags.
- [x] A live user-account test verifies native bold, italic, blockquote, and preformatted entities without visible raw markup.

## Execute every AI Request through Pi

**What to build:** Replace the direct chat completion path with a dedicated Pi Agent Engine while preserving Telegram authorization, reply branches, streaming presentation, memory augmentation, and account-aware formatting.

**Blocked by:** Render account-aware Telegram AI answers; Augment AI Conversations and learn from them.

- [x] A dedicated Node service exposes validated health, streaming run, and cancellation endpoints without Telegram, Matrix, or memory credentials.
- [x] Pi persists one Agent Session tree per root Trigger Message, and every successful AI Answer stores its Pi session and terminal entry identifiers.
- [x] Continuations resume from the mapped entry, replies to earlier AI Answers branch from that entry, and runs within one Agent Session are serialized.
- [x] Owner runs receive the configured Pi filesystem and shell tools; delegated runs receive only constrained web search, page retrieval, and QuickJS execution.
- [x] `pi-web-access` is pinned and wrapped so delegated fetching is HTTP(S)-only, Exa search uses raw results, and local files, browser cookies, video handling, and GitHub cloning are unavailable.
- [x] QuickJS runs in a fresh guest runtime with no host APIs and bounded code, time, memory, stack, and output.
- [x] Telegram shows transient sanitized Tool Snapshots, streams the final answer in the existing message, and supports cancellation without retaining tool output in the completed answer.
- [x] The Pi service reuses the existing OpenAI-compatible model configuration and does not expose secrets in logs, responses, or health data.
- [x] Unit and integration tests cover API validation, streaming, cancellation, tool policies, code limits, session continuation, branching, persistence, and failure without external credentials.
- [x] The local Docker Compose stack builds healthy `pi`, `memory`, and `ai` services and passes owner, delegated, web, calculation, continuation, fork, memory, and cancellation live checks in the allowlisted Telegram group.

## Describe reply-chain attachments without storing raw media

**What to build:** Include bounded attachment descriptions in AI context and per-user memory while keeping raw Telegram media transient.

**Blocked by:** Execute every AI Request through Pi.

- [x] Photos and image documents are normalized in memory and described through a non-persistent authenticated Pi vision endpoint.
- [x] PDFs and UTF-8 text-like files are extracted in memory and summarized, while unsupported media contributes safe metadata only.
- [x] The current request attachment and up to three reply-chain attachments become labeled untrusted context; attachment-only requests receive a default instruction.
- [x] Memory observations attribute generated descriptions as content the subject shared and do not infer ownership, authorship, or truth about the subject.
- [x] Raw bytes, Telegram URLs, and temporary paths are absent from Pi Agent Sessions, SQLite state, Zvec observations, and logs.
- [x] Unit, provider, and live Telegram tests cover image analysis, document extraction, endpoint authentication, context assembly, memory ingestion, and non-persistence.

## Ingest memory from Saved Messages forwards

**What to build:** Let the owner privately request natural memory ingestion by forwarding a source message to Saved Messages, without posting `/ai_memory` in the source chat.

**Blocked by:** Revise a replied user's profile from Telegram; Describe reply-chain attachments without storing raw media.

- [x] Only a newly forwarded message in the authenticated owner's Saved Messages activates the trigger; direct Saved Messages text and forwards to other chats retain their existing behavior.
- [x] Telefire uses Telegram's Saved Messages source peer and message ID to fetch the original message and ingest the same bounded ancestor chain as bare `/ai_memory`, including per-author attachment descriptions.
- [x] The forwarded copy remains in Saved Messages; reaction tags are best-effort for Premium accounts, non-Premium success is silent, and every failure receives a private reply that distinguishes unavailable source metadata from retryable processing failure.
- [x] A persistent adapter receipt prevents duplicate delivery of one saved copy from repeating ingestion, while re-forwarding the same source remains an intentional retry.
- [x] Missing, private, deleted, or inaccessible source messages fail without guessing attribution or starting an AI request from forwarded command text.
- [x] Focused, full-suite, and live Telegram tests pass against the rebuilt local Docker stack.
