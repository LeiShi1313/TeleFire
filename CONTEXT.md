# Telefire

Telefire is a personal automation tool for Telegram and Matrix accounts. This glossary names the user-facing automation concepts so command behavior stays precise.

## Language

**AI Request**:
A request to have the agent engine produce an AI answer from chat context. It is created either by a trigger message or by an authorized continuation message.
_Avoid_: AI command, ask mode

**Agent Run**:
The bounded work performed for one AI request. It may contain multiple model turns and tool calls, and it ends when an AI answer is produced, the run fails, or it is cancelled.
_Avoid_: Completion, process, session

**Agent Engine**:
The runtime responsible for executing agent runs, including model interaction, tool calls, retries, and run events. Telefire delegates agent execution to it while retaining chat policy and presentation responsibilities.
_Avoid_: AI provider, bot, tool runner

**Agent Session**:
The internal persisted transcript tree owned by the agent engine, including model messages, tool calls, results, branches, and compaction state. One begins with a root trigger, is authoritative for agent history, and is distinct from the user-facing AI conversation and any chat-platform login session.
_Avoid_: AI Conversation, Telegram session, reconstructed prompt

**Tool**:
A named executable capability available during an agent run, with bounded input and output. Web search, page retrieval, and code execution are tools rather than skills.
_Avoid_: Skill, command, plugin

**Tool Policy**:
The set of tools and skills an agent run may use based on the requester's authority. Owner and delegated requests may have different tool policies even though both use `/ai`.
_Avoid_: Whitelist, tool list, agent mode

**Skill**:
Reusable instructions and supporting resources that guide the agent engine through a specialized workflow, potentially using one or more tools. A skill does not itself define the isolation or authorization boundary of a tool.
_Avoid_: Tool, prompt, extension

**AI Conversation**:
A reply branch opened by an `/ai` trigger and continued without a command by replying directly to AI answers. An explicit `/ai` elsewhere in the reply ancestry rejoins the nearest completed AI turn; replying to an earlier AI answer creates a new branch with the same preceding context.
_Avoid_: Session, thread

**AI Answer**:
An answer produced for an AI request and sent from the owner's Telegram account.
_Avoid_: Bot message, completion

**Continuation Message**:
A message from the owner or a whitelisted user that replies directly to an AI answer and makes the next AI request without repeating `/ai`.
_Avoid_: Follow-up command, implicit trigger

**Context Participant**:
A human author represented by a message in the reply context of an initial AI request. Their stored memory may be supplied as labeled background for that request, and their authored contribution may be ingested into their own user memory after a successful answer. AI answers are not context participants, and a continuation message does not necessarily reselect participants from earlier agent-session history.
_Avoid_: Thread member, chat member

**Explicit Subject Reference**:
An unambiguous, platform-resolved reference to a memory subject, such as a Telegram mention linked to a user account. The referenced subject may be selected for memory retrieval even when they did not author a message in the reply context. Plain display-name text is not an explicit subject reference.
_Avoid_: Name match, inferred mention

**Memory Subject Discovery**:
The process of resolving a person reference to a canonical memory subject before retrieving that subject's memory context. Discovery may use an explicit subject reference directly or search memory evidence within the current scope for a previously established natural-language reference. Dynamic discovery does not fall back automatically to other scopes. Discovery identifies a subject; it does not by itself return that subject's full memory context.
_Avoid_: Memory retrieval, alias lookup

**Fact Attribution**:
The association between a derived fact and the canonical memory subject that the fact describes. It is distinct from observation authorship: a person may assert a fact about another deterministically referenced subject while remaining the author of the source observation.
_Avoid_: Message author, source attribution

**Initial Prompt**:
The text following `/ai` in a trigger message. It is the primary instruction for the first AI request in an AI conversation.
_Avoid_: Command text, opening question

**Follow-up Prompt**:
The text of a continuation message. It is the primary instruction for the next AI request in an AI conversation.
_Avoid_: Reply text, continuation command

**Reply Context**:
The ancestor messages preceding a trigger message in its Telegram reply branch. They are reference material for the initial prompt, not instructions by themselves.
_Avoid_: Quoted message, chat history, thread

**Streaming Answer**:
An AI answer shown progressively by editing one Telegram message as response text arrives.
_Avoid_: Live reply, streamed reply

**Tool Snapshot**:
A bounded, sanitized, temporary view of the active tool and its result shown while an agent run is in progress. It is replaced when the final AI answer begins and is not part of the completed answer.
_Avoid_: Tool log, reasoning, final answer

**Trigger Message**:
A message beginning with `/ai` that makes an explicit AI request. It starts a new AI conversation when its reply ancestry has no completed AI turn; otherwise it rejoins the nearest one.
_Avoid_: Command message

**User Memory**:
The scoped view of facts, experiences, and observations associated with a person entity. It is a retrieval view over contextual memory, not a separately owned record collection and not an automatic cross-scope profile.
_Avoid_: User database, global profile

**Chat Memory**:
The complete Hindsight bank for one chat scope, including source episodes and chunks, facts, experiences, entities, relationships, observations, and revisions. It belongs to the chat scope rather than to any one participant.
_Avoid_: User profile, transcript

**Fact**:
An extracted memory unit connected to its source evidence, time, and relevant entities. A fact may represent a self-report, decision, verified state, or attributed third-party claim; the term does not guarantee objective truth.
_Avoid_: Ground truth, profile field

**Episode**:
An immutable, ordered evidence bundle submitted to one memory scope. It may contain multiple messages, actors, replies, quotations, explicit references, attachment descriptions, event times, and optional source references; it is the provenance for extracted memory rather than an author-owned fact.
_Avoid_: Single-user observation, profile update

**Memory Entity**:
A person, project, organization, place, object, event, concept, or scoped shared term connected to memory. Integrations provide canonical IDs for deterministically known entities; inferred names and aliases remain scoped and unresolved until evidence supports a connection.
_Avoid_: Telegram user, subject row

**Memory Subject**:
A memory entity used as the focus of a particular recall or correction operation. Being the subject of a fact is distinct from authoring its source episode and does not make that entity the owner of the memory.
_Avoid_: Message author, memory owner

**Memory Scope**:
A chat, workspace, project, shared knowledge source, or equivalent trust boundary mapped to exactly one Hindsight bank. Trusted application code selects the Primary Bank before agent execution. Additional banks may be consulted only through bounded Bank References recalled from the Knowledge Directory Bank.
_Avoid_: Relevance filter, model-selected namespace

**Primary Bank**:
The Hindsight bank selected from the current chat, workspace, or other request origin. It is always recalled for an AI request and remains distinct from any additional knowledge source consulted while answering.
_Avoid_: Active bank, attached bank

**Knowledge Directory Bank**:
The deployment-wide, platform-neutral, always-consulted Hindsight bank containing ordinary memory about available knowledge sources, what they represent, how people refer to them, and how they relate. Telegram, QQ, and non-chat knowledge sources publish into the same directory. It may contain Bank References, but it is not an alias table or a store for arbitrary globally visible subject matter.
_Avoid_: Global Bank, alias bank, bank registry

**Bank Reference**:
Evidence in the Knowledge Directory Bank that describes a knowledge source and carries a trusted, machine-readable reference to its Hindsight bank. Natural-language description supplies meaning; the reference permits bounded traversal without letting prompt text choose an arbitrary bank. It is routing evidence rather than an access grant; requester policy separately decides whether the referenced bank may be consulted.
_Avoid_: Alias, attachment, raw bank ID

**Directory Publication**:
An explicit, owner-authorized evidence submission to the Knowledge Directory Bank describing exactly one knowledge source and carrying exactly one trusted Bank Reference. Its evidence consists only of adapter-resolved source metadata and the owner's description; publication never samples messages or recalls the referenced bank. Each `/ai_directory` invocation adds timestamped evidence and never silently replaces earlier evidence, so a correction or change must be stated explicitly in the description. Publication idempotently ensures the referenced bank exists but never changes cross-chat eligibility or enables capture, Dream, or backfill for it. Hindsight extracts ordinary facts and observations from the submitted description; every derived directory memory remains associated with exactly that one Bank Reference and never consolidates evidence from another source. The publication is not itself a pre-extracted fact or source profile.
_Avoid_: Alias registration, bank attachment, global memory update

**Directory Source Policy**:
The adapter-specific rules governing which external resources may become Directory Publications and how their identities are verified. Telegram may resolve accessible chats and channels through native identities, forwards, or message links; QQ resolves only groups accessible through the connected OneBot account. Private QQ conversations cannot be published as shared knowledge sources.
_Avoid_: Directory schema, memory visibility

**Referenced Bank**:
A Memory Scope identified by a Bank Reference recalled during an AI request. Identification alone does not mean its memory was loaded.
_Avoid_: Attached bank, secondary scope

**Consulted Bank**:
A Referenced Bank that the Agent Run actually queried because directory evidence showed it was relevant to the current request. It is transient to the current AI Conversation branch and does not become permanently attached to the Primary Bank. An AI Answer may name its human-readable source when attribution matters, but canonical bank identities remain internal diagnostics.
_Avoid_: Enabled bank, mounted bank

**Cross-Bank Retrieval**:
A bounded recall of a Consulted Bank reached through relevant Bank Reference evidence from the Knowledge Directory Bank. The owner may consult any relevant bank. A Whitelisted User may always use the Primary Bank but needs an explicit per-bank grant before another bank can be consulted. A Consulted Bank may suggest another source, but only a subsequent filtered directory lookup can resolve it into another Bank Capability. One Agent Run permits at most one additional directory lookup and two non-primary Consulted Banks; ambiguity, cycles, missing grants, or exhausted budgets stop traversal. Banks are not enumerated or searched collectively, and raw IDs in prompts cannot select one.
_Avoid_: Bank permission, global search, automatic attachment

**Bank Grant**:
An owner-controlled authorization allowing one specific Whitelisted User to consult one specific non-primary bank through Cross-Bank Retrieval. The grantee is the exact platform-qualified actor identity, such as `telegram:user:<id>` or `qq:user:<id>`; accounts on different platforms are never inferred to be the same person and must be granted separately. The authorization is deployment-wide for that `(actor identity, target bank)` pair and is not restricted to the chat where its command was issued. A grant can be issued only while that user is whitelisted, neither exposes the bank to other whitelisted users nor enables capture, and is deleted when the user's AI whitelist access is revoked. It controls who may trigger retrieval, not who may read an AI Answer delivered into a shared chat. Grant commands may use an adapter-native source selector only when the command and source belong to the same chat platform, such as a Telegram `@username` or a QQ group number. Selecting a source on another platform requires its full canonical bank ID; this avoids ambiguous cross-platform name resolution. In either case trusted host code resolves and validates the selector against a Directory Publication, while source-like text in an ordinary AI prompt grants no authority.
_Avoid_: Directory Publication, bank attachment, group membership

**Effective Bank Access**:
The Primary Bank plus the non-primary banks the current requester may consult during one Agent Run. It is recalculated for every run and supplied to the agent as advisory policy context, while trusted tools enforce it for new retrieval. In v1, an Agent Session continued by another requester may still contain earlier evidence outside their Effective Bank Access; prompt policy reduces but does not eliminate disclosure risk.
_Avoid_: Bank Capability, permanent permission, secure session boundary

**Participant Access Context**:
Hidden advisory context regenerated for each Agent Run that identifies the current requester and summarizes which non-owner human authors represented in the bounded reply or chat context may use each knowledge source already present in the conversation or offered during the run. Mentions alone do not reveal a person's grants. The context omits the unrestricted owner and unrelated grants. Only the current requester's Effective Bank Access controls tool capabilities; another participant's Bank Grant never expands the current run.
_Avoid_: Complete grant directory, shared permission, authorization boundary

**Bank Capability**:
An opaque, run-scoped handle issued by trusted application code after a recalled Bank Reference passes the requester's Bank Grant policy. Initial Primary Bank and filtered Knowledge Directory recall happen automatically, but the Agent Run must explicitly choose a capability before the referenced bank is consulted. The agent may reformulate the downstream query but never receives authority from a raw bank ID, and the capability is not persisted as access in the Agent Session.
_Avoid_: Bank Grant, raw bank ID, permanent attachment

**Shared Knowledge Bank**:
A non-chat Memory Scope containing a reusable body of subject matter, such as project documentation, a news source, or a topical archive. It is discoverable through the Knowledge Directory Bank and remains independently ingested, configured, and governed.
_Avoid_: Global Bank, directory bank

**Memory-Enabled Scope**:
A memory scope explicitly configured for automatic capture. In Telefire this permits successful AI reply threads and scheduled dream scans to ingest chat evidence; it is independent from who may invoke `/ai`, and an explicit `/ai_memory` does not enable the scope automatically.
_Avoid_: AI allowed chat, whitelisted chat

**Observation**:
A consolidated, revisable synthesis derived from one or more facts and their evidence. It can summarize a stable pattern or current understanding, may lag the newest facts, and must not hide newer contradictory evidence.
_Avoid_: Source episode, unsupported profile

**Memory Evidence**:
The source episode, chunk, quotation, timestamp, and authorship metadata that support an extracted fact or observation. Evidence proves what was observed or said, not that every claim in it is true.
_Avoid_: Citation label, ground truth

**Dream Cycle**:
A scheduled integration job that fetches bounded messages from memory-enabled scopes, groups nearby reply roots into deterministic conversation-segment documents, submits those document updates to Hindsight with bounded concurrency, and checkpoints each accepted chronological prefix. Existing thread documents keep their identities, and every segment preserves actor, timestamp, source, and reply relationships. It is not an answer-agent reflection call; reply grouping is context enrichment rather than an ingestion requirement.
_Avoid_: Cron recall, model dream

**Memory Backfill**:
An owner-triggered, bounded historical scan of one explicit memory scope by rolling days or latest seed-message count. It reuses Dream ingestion, leases, receipts, and thread enrichment, but does not require automatic enablement and never changes the scheduled Dream cursor.
_Avoid_: Scheduled Dream, global chat import

**Ingestion Receipt**:
Durable delivery state recording that a normalized source event or version was assigned to a Hindsight document. Receipts and scan cursors provide retry and overlap idempotency but are not a second memory store.
_Avoid_: Memory fact, source message contract

**Runtime Inference**:
A new connection or recommendation formed while answering from recalled memory, such as matching a person's stated preference to a venue. It must be communicated with appropriate certainty and is not automatically persisted as a fact.
_Avoid_: Remembered fact, automatic profile update

**Memory Context**:
A structured, bounded, and labeled selection of scoped facts, experiences, observations, entities, and evidence relevant to a request. It augments the request but is not itself the main prompt, and each memory client decides how to render it.
_Avoid_: Memory prompt, full graph dump

**Memory Client**:
An application that submits observations, requests memory context, or revises memory through the standalone memory module. Telefire is a memory client, not a memory subject.
_Avoid_: User, consumer, plugin

**Memory Update Command**:
An owner-only command that ingests or revises scoped memory using a replied-to chain, forwarded source, pasted source link, or explicit instruction as evidence. It may affect any entities described by the evidence rather than only its author.
_Avoid_: User-profile command, raw database edit

**Revision**:
An explicit or evidence-backed change that supersedes, retracts, suppresses, or corrects scoped memory while preserving its history and provenance. A newer statement may change current state without erasing what was previously observed.
_Avoid_: Hard delete, silent overwrite

**Whitelisted User**:
A non-owner chat user who is explicitly allowed to make AI requests under the restricted tool policy. Whitelisting permits use of the current Primary Bank but does not grant Cross-Bank Retrieval; each additional bank requires an explicit grant. The owner is always allowed and does not need to be whitelisted.
_Avoid_: Allowed user, approved user

**Whitelist Command**:
An owner-only trigger message that grants or revokes a non-owner user's ability to make AI requests.
_Avoid_: Allow command, permission command
