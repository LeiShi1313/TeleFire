---
status: accepted
---

# Use Pi as the agent engine

Telefire will delegate every AI request to a dedicated Pi agent service instead of maintaining its own model-and-tool loop or splitting `/ai` into answer and agent modes. Telefire retains chat authorization, rate limits, reply-branch semantics, memory augmentation, and Telegram presentation, while Pi owns model interaction, tool execution, retries, compaction, and authoritative agent-session history. Pi runs unsandboxed inside its own Docker container with a private interface, its own sessions and workspace, and no Telefire runtime volume or Telegram and Matrix credentials; this keeps one `/ai` experience while accepting Pi and Node.js as runtime dependencies.

Each root `/ai` trigger creates one Pi session tree. Telefire maps every AI answer to its terminal Pi entry so continuations resume from that point and replies to earlier answers create branches within the same session; runs against one session are serialized while independent root sessions may run concurrently.

The Pi service uses the Pi SDK internally and exposes only a private run-oriented HTTP API to Telefire. A run request streams bounded NDJSON events for answer text, tool activity, completion, and failure, with separate health and cancellation operations; Pi SDK models, RPC commands, and session-file details do not cross the service boundary.

Pi uses the existing deployment-wide OpenAI-compatible chat configuration: base URL, API key, model, and optional reasoning effort. V1 has no per-user model selection, model router, fallback provider, or retained direct-completion path in Telefire.

Pi may perform its ordinary provider-level retries within a run, but Telefire never automatically replays a failed or disconnected run. A failure is reported in the streaming message and the user may resubmit from the last completed AI answer; this avoids repeating tool side effects whose outcome may be unknown.

Telefire retrieves a fresh, bounded memory context for the requester and supplies it to each run as labeled background. Pi has no memory-mutation tool in v1: observation ingestion and explicit revision remain operations of the standalone memory module, outside the agent session and its tool authority.

Reply-chain attachments are analyzed through a separate authenticated Pi endpoint before an Agent Run is created. Telefire downloads bounded Telegram media into memory, normalizes images or extracts bounded document text, and sends one transient analysis request. The analysis call uses Pi AI directly rather than an Agent Session so raw bytes and extracted source text are not written to session history. The resulting generated description is labeled untrusted, supplied to the Agent Run, and may be ingested as an attributed memory observation; raw media is never stored by Telefire memory.

Every run carries an owner or delegated tool policy selected by Telefire. Owner runs may use the service's full configured tools and skills, while `/ai_allow` grants non-owners only the delegated policy and never implicitly grants unrestricted shell or filesystem access.

Tools execute automatically within the selected policy; v1 does not add per-call approval prompts. While a tool is active, Telefire may show a bounded, sanitized snapshot of its input and result in the same message used for streaming. The snapshot is transient and disappears when the final assistant turn starts, so the completed message contains only the answer. Telefire allows the requester to cancel the active run and never exposes model reasoning or unbounded raw tool output.

The Pi service disables ambient project and host resource discovery and loads only an explicit, vetted catalog of skills and extensions built into or mounted specifically for the service. The execution workspace cannot introduce instructions or executable extensions merely by containing Pi configuration or Agent Skills files.

The v1 catalog is deliberately small: every authorized run may use web search, web page retrieval, and ephemeral JavaScript execution in an embedded QuickJS/WASM runtime, while only owner runs may use Pi's general shell and filesystem tools in the persistent agent workspace. The delegated code tool exposes no host filesystem, environment, shell, subprocess, or network APIs and applies per-call time, memory, and output limits. No other skill is included until a concrete workflow requires it.

Web access is provided by a version-pinned `pi-web-access` extension configured for headless raw-result search and content extraction. V1 uses zero-configuration Exa search, keeps SSRF protection enabled, and disables the interactive curator, browser-cookie access, video and YouTube processing, GitHub cloning, and the bundled librarian skill.
