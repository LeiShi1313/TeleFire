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
A reply branch opened by an `/ai` trigger and continued by replying directly to AI answers. Replying to an earlier AI answer creates a new branch with the same preceding context.
_Avoid_: Session, thread

**AI Answer**:
An answer produced for an AI request and sent from the owner's Telegram account.
_Avoid_: Bot message, completion

**Continuation Message**:
A message from the owner or a whitelisted user that replies directly to an AI answer and makes the next AI request without repeating `/ai`.
_Avoid_: Follow-up command, implicit trigger

**Context Participant**:
A Telegram user represented by a message in the reply context or AI conversation. Their authored messages may be ingested into their own user memory, but their stored memory is not automatically supplied when another participant makes a request.
_Avoid_: Thread member, chat member

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
A message beginning with `/ai` that opens an AI conversation. It may stand alone or reply within an existing chat chain.
_Avoid_: Command message

**User Memory**:
Persisted background associated with one memory subject, composed of a subject profile and scoped memories. In v1 it may be supplied when that subject makes an AI request, not merely because the subject appears in another person's reply context.
_Avoid_: Memory file, chat history

**Subject Profile**:
Explicitly revised facts about a memory subject that are safe to reuse across scopes where that subject participates. It is stored as one Markdown-text record in Zvec, not as a separately synchronized file. Observations do not promote scoped memory into the subject profile automatically.
_Avoid_: Global memory, contact profile

**Chat Memory**:
Facts and episodes learned about one memory subject within a chat scope. It is reusable only within that same chat.
_Avoid_: Local profile, conversation history

**Fact**:
A relatively stable statement about a memory subject extracted from observations and retained within a memory scope. A fact is not a timestamped occurrence and is not promoted automatically into the subject profile.
_Avoid_: Profile fact, episode

**Episode**:
A timestamped memory of something a memory subject said, asked, or did within a scope. It records an occurrence rather than a stable profile fact; platform-specific provenance may be retained when available but is not required.
_Avoid_: Fact, history item, event

**Memory Subject**:
A person or other entity that user memory describes. Callers identify a subject with a namespaced key rather than a platform-specific user type.
_Avoid_: Telegram user, account

**Memory Scope**:
A context within which scoped memory may be reused, such as a chat, workspace, project, or conversation. Facts and episodes from a scope are never retrieved outside it unless a memory client requests that scope explicitly.
_Avoid_: Chat ID, namespace

**Observation**:
Timestamped content authored by one memory subject and offered within one memory scope as potential material for facts or episodes. Each ingestion supplies one subject, one scope, and one text payload. The memory module retains the text under its own generated identifier for auditing, exact-retry detection, and re-extraction; opaque origin metadata is optional and no platform-specific message identifier is required. Observations are not returned in normal memory context.
_Avoid_: Source message, memory input

**Memory Context**:
A structured, bounded, and labeled selection of profiles, scoped facts, and episodes relevant to a request. It augments the request but is not itself the main prompt, and each memory client decides how to render it.
_Avoid_: Memory prompt, retrieved history

**Memory Client**:
An application that submits observations, requests memory context, or revises memory through the standalone memory module. Telefire is a memory client, not a memory subject.
_Avoid_: User, consumer, plugin

**Memory Update Command**:
An owner-only command sent in reply to a person's message that revises that person's subject profile using the replied-to message as source context.
_Avoid_: Profile command, remember message

**Revision**:
An explicit natural-language request to add, correct, or stop retrieving subject-profile or scoped-memory content, optionally accompanied by evidence text. The memory module finds and applies the relevant change, so callers do not need memory record identifiers. In v1, a request to forget removes derived memory from augmentation but does not purge retained observations. Automatic ingestion does not replace an existing memory merely because new content may conflict with it.
_Avoid_: Memory edit, update

**Whitelisted User**:
A non-owner Telegram user who is explicitly allowed to make AI requests under the delegated tool policy. Whitelisting does not grant the owner's shell or filesystem authority; the owner is always allowed and does not need to be whitelisted.
_Avoid_: Allowed user, approved user

**Whitelist Command**:
An owner-only trigger message that grants or revokes a non-owner user's ability to make AI requests.
_Avoid_: Allow command, permission command
