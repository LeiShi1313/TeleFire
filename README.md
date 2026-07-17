# TeleFire

TeleFire is a CLI for Telegram, QQ/OneBot, and Matrix automation.

The current codebase is `uv`-first, Python 3.14+, and built around explicit runtime layers instead of protocol logic living directly in command classes.

## Highlights

- Telegram runtime on Telethon
- QQ runtime through an authenticated OneBot 11 reverse WebSocket
- Matrix runtime on mautrix
- optional native Matrix E2EE with Olm/Megolm and cross-signing
- shared command runner for one-shot and long-running commands
- account-aware config in `~/.telefire/config.toml`
- Telegram session storage in `~/.telefire/telegram/`
- Matrix session, sync, and state storage in `~/.telefire/matrix/<account>/`
- Fire command wrappers with real signatures, so required args can be positional

## Install

From the repo:

```bash
uv sync
uv run telefire --help
```

Enable Matrix E2EE commands:

```bash
uv sync --extra e2ee
```

If `python-olm` fails to build with a CMake policy error, use:

```bash
CMAKE_POLICY_VERSION_MINIMUM=3.5 uv sync --extra e2ee
```

One-shot:

```bash
uvx telefire --help
```

Global install:

```bash
uv tool install telefire
pipx install telefire
```

## Run Pattern

```bash
telefire telegram <command> [args...]
telefire matrix <command> [args...]
telefire init
```

## Setup

Telegram requires a user API ID and API hash from https://my.telegram.org.

Run the interactive setup:

```bash
uv run telefire init
```

That writes `~/.telefire/config.toml`.
`telefire init` configures the default Telegram and Matrix accounts. Optional named accounts can be added manually later.

Before running commands, validate the current setup:

1. Check config:

```bash
cat ~/.telefire/config.toml
```

2. Validate Telegram:

```bash
uv run telefire telegram get_entity me
```

3. Validate Matrix:

```bash
uv run telefire matrix whoami
```

The first Telegram command will prompt for login if the selected session file does not exist yet.

Create a named Telegram session explicitly with:

```bash
uv run telefire telegram login --account work
```

The first Matrix command can bootstrap from the configured password, then persist `access_token` and `device_id` into the account store and reuse that session on later runs.

## Config

The normal setup path is the default account under `[telegram]` and `[matrix]`.

```toml
[telegram]
api_id = 123456
api_hash = "..."
session_name = "telefire"
store_dir = "/home/you/.telefire/telegram"

[telegram.work]
session_name = "work"

[matrix]
base_url = "https://matrix.example.com"
user_id = "@you:example.com"
device_name = "telefire"
store_dir = "/home/you/.telefire/matrix/default"
password = "..."
```

Optional extra accounts can be added manually as subtables:

```toml
[telegram.work]
session_name = "work"

[matrix.work]
base_url = "https://matrix.work.example"
user_id = "@you:work.example"
device_name = "telefire"
store_dir = "/home/you/.telefire/matrix/work"
password = "..."
```

Notes:

- Telegram defaults to the config under `[telegram]`.
- Telegram uses `--account` to resolve an optional configured session alias.
- Telegram also accepts `--session` as a low-level override.
- Matrix defaults to the config under `[matrix]`.
- Matrix uses `--account` to select an optional named account and store directory.

## Storage Layout

Telegram:

- `~/.telefire/telegram/telefire.session`
- `~/.telefire/telegram/work.session`

Matrix:

- `~/.telefire/matrix/default/session.json`
- `~/.telefire/matrix/default/sync_store.json`
- `~/.telefire/matrix/default/state_store.bin`
- `~/.telefire/matrix/default/crypto.db`
- `~/.telefire/matrix/default/crypto_pickle.key`
- `~/.telefire/matrix/work/session.json`

`crypto.db` stores Olm/Megolm sessions and Matrix device/cross-signing state.
`crypto_pickle.key` protects the local libolm account pickle. Keep both private.

## Usage

Inspect available commands:

```bash
uv run telefire --help
uv run telefire telegram --help
uv run telefire matrix --help
uv run telefire telegram COMMAND --help
uv run telefire matrix COMMAND --help
```

Telegram examples:

```bash
uv run telefire telegram get_entity me
uv run telefire telegram get_entity me --session=work
uv run telefire telegram get_all_chats
uv run telefire telegram list_messages --chat=coder_ot --user=Fangliding
uv run telefire telegram search_messages --chat=coder_ot --query='keyword'
```

### Telegram AI and Memory

The local deployment is split into four independently owned Compose projects:

- `memory/compose.yml` runs Hindsight with a locally built Control Plane. It has
  no Telefire, Telegram, or QQ dependency.
- `agent/compose.yml` runs the Pi service and Agent Playground. It depends only
  on the Memory Stack HTTP API.
- `docker-compose.yml` runs the Telegram and QQ/OneBot adapters. They call Pi
  and Hindsight over their private Docker networks and keep separate state
  databases.
- `proxy/compose.yml` provides one loopback dashboard ingress across the Memory
  and Agent networks using
  [`nginx-proxy`](https://github.com/nginx-proxy/nginx-proxy) virtual hosts.

Create private environment files from each stack's template:

```bash
cp memory/.env.example memory/.env
cp agent/.env.example agent/.env
cp .env.example .env
```

Configure the memory and agent providers in their respective files. Set the same
private token as `PI_AGENT_TOKEN` in `agent/.env` and `TELEFIRE_PI_TOKEN` in the
root `.env`. The memory embedding model and dimension define one fixed vector
space; changing either requires explicit re-ingestion and re-embedding. Reasoning
effort accepts `none`, `minimal`, `low`, `medium`, `high`, `xhigh`, or `max` when
supported by the selected model.

`TELEFIRE_AI_EDIT_CADENCE` is the account-wide minimum interval between Telegram
message edits and defaults to 4 seconds. Intermediate stream updates are skipped
when the edit slot is busy; final answers wait for the next slot.
`TELEFIRE_MEMORY_COMMAND_DELETE_DELAY` controls how many seconds accepted owner
memory commands remain visible and defaults to 3 seconds.

### Docker compose

Clone Hindsight once, then build the pinned v0.8.4 Control Plane with the local
bank-name patch. The source clone stays unchanged; the build script exports the
exact upstream revision into a temporary build directory:

```bash
gh repo clone vectorize-io/hindsight "$HOME/workspace/cloned/hindsight"
./memory/build-hindsight-control-plane.sh "$HOME/workspace/cloned/hindsight"
```

Start the Memory Stack first, then the Agent Stack, Telefire, and the dashboard
proxy:

```bash
docker compose --env-file memory/.env -f memory/compose.yml up -d --build
docker compose --env-file agent/.env -f agent/compose.yml up -d --build
docker compose --env-file .env up -d --build
docker compose -f proxy/compose.yml up -d
```

All published ports bind to loopback because memory and agent sessions contain
private data:

- Memory API: `http://127.0.0.1:18888`
- Private Pi API: `http://127.0.0.1:18790`

Both dashboards share port `18865` and are selected by hostname:

- Native Hindsight UI: `http://hindsight.telefire.localhost:18865`
- Agent sessions and Playground: `http://sessions.telefire.localhost:18865`

Set `DASHBOARD_PROXY_EXPOSE_PORT` when starting `proxy/compose.yml` to override
the shared port. The proxy joins only `memory-platform` and `agent-platform`;
the embedding network remains backend-only. `nginx-proxy` reads Docker metadata
through the read-only Docker socket, so this ingress must remain loopback-bound
and should not be treated as an authenticated public gateway.

The playground can run a plain model session with all tools disabled, or a Pi
session with the same constrained web, code, and bank-pinned memory tools used by
the chat adapters. It exposes the exact prepared request, recalled evidence,
and transient tool snapshots in the browser without exposing provider keys or the
Pi token. Pi's HTTP API remains a private authenticated interface, not an
OpenAI-compatible endpoint.

`TELEGRAM_API_ID` and `TELEGRAM_API_HASH` must be set in the root `.env`. The
userbot container receives neither model-provider nor embedding credentials.

The OneBot adapter is optional. Configure a separate random
`TELEFIRE_ONEBOT_TOKEN`, set `TELEFIRE_ONEBOT_SELF_ID` to the logged-in QQ
account, and bind `TELEFIRE_ONEBOT_PUBLISH_HOST` only to an address reachable by
NapCat. Add an enabled NapCat reverse-WebSocket client with:

- URL `ws://<telefire-host>:<TELEFIRE_ONEBOT_PUBLISH_PORT>/onebot`
- the same bearer token
- array message format
- self-message reporting enabled
- a bounded reconnect interval and heartbeat

Do not expose this listener without the token or reuse another OneBot
integration's token. The health endpoint at `/healthz` reports both process
health and whether NapCat is currently connected.

A Telegram API login/session still needs to be initialized once:

```bash
docker compose run --rm -it ai telefire telegram login --account default
```

Then restart the AI service:

```bash
docker compose restart ai
```

The QQ adapter starts with the rest of the root Compose project:

```bash
docker compose --env-file .env up -d --build onebot-ai
curl http://127.0.0.1:${TELEFIRE_ONEBOT_PUBLISH_PORT:-18867}/healthz
```

Memory administration can be run quietly from the local CLI. These commands use
the running OneBot adapter and print JSON locally; they do not send or delete QQ
messages:

```bash
uv run telefire onebot memory dream-enable 694769138 "BetterGI v2"
uv run telefire onebot memory status 694769138
uv run telefire onebot memory backfill 694769138 500
uv run telefire onebot memory dream-disable 694769138
```

When running the command inside Compose, use
`docker compose exec onebot-ai python -m telefire.cli ...` with the same
arguments after `telefire`. Backfill defaults to message-count mode; pass
`--mode=days` for a bounded day window.

The standalone Ollama Compose stack joins the external `ollama-embedding` network.
Point Hindsight at that service in `memory/.env`:

```bash
MEMORY_EMBEDDING_BASE_URL=http://ollama-embedding-ollama-1:11434/v1
MEMORY_EMBEDDING_API_KEY=ollama
```

For a host Ollama process instead, use
`MEMORY_EMBEDDING_BASE_URL=http://host.docker.internal:11434/v1`.
Compose maps that hostname to the Linux host gateway. In Docker,
`http://127.0.0.1:11434` is the Hindsight container itself and must not be used.

The root `telefire-runtime` volume contains the separate Telegram `ai.db`, QQ
`onebot-ai.db`, and Telegram sessions. `memory-hindsight-data` contains
Hindsight, and Pi's private Agent Sessions live in `pi-agent-data`.

Commands and reply behavior:

The command set below is shared by Telegram and QQ unless a bullet explicitly
describes a Telegram-only workflow. QQ uses plain-text answer formatting because
its clients do not reliably render Telegram Markdown or HTML. NapCat does not
provide message editing, so QQ receives one temporary `Thinking...` reply and
one final reply; TeleFire then recalls the temporary message. Intermediate Pi
tool and text snapshots stay internal.

- `/ai <question>` starts a conversation. A replied message chain is reference
  context; only text after `/ai` is the current instruction. After a successful
  answer, human-authored messages in that bounded chain are ingested under their
  respective users. Relevant scoped memory is retrieved for the requester and
  the human reply-chain participants.
- `/ai10 <question>` starts with up to 10 messages immediately before the command
  as additional chat context. Any number from 1 through
  `TELEFIRE_AI_MAX_CONTEXT_MESSAGES` is accepted. When used in a reply, TeleFire
  merges the chronological recent messages with the reply path, removes
  duplicates, and preserves reply relationships. A prior AI answer on that reply
  path still selects the Agent Session; recent-only messages can supply memory
  participants but are not automatically retained as reply-thread evidence.
- Reply directly to an AI answer without `/ai` to continue. Reply to an older
  AI answer to fork from that point.
- Reply to a photo, image document, PDF, or UTF-8 text file with `/ai <question>`
  to include a generated description in the reference context. An attachment on
  the `/ai` message itself is also supported; attachment-only requests use
  `Describe the attached content.` as their instruction.
- Reply to a user's message with `/ai_allow` or `/ai_deny` to manage delegated
  access. The owner is always allowed. A successfully executed owner command is
  deleted while its acknowledgement remains visible; usage and execution failures
  stay visible for inspection and retry.
- `/ai_directory [source] [description]` publishes one owner-approved group or
  channel in the shared Knowledge Directory without reading that source's memory.
  With no source selector it publishes the current group/channel and treats the
  suffix as a description. Telegram also accepts an accessible `@username`, a
  `t.me` message link, or the trusted origin of a replied forwarded message. QQ
  accepts an accessible positive group ID. Telegram users, QQ private chats,
  hidden forward origins, inaccessible sources, and cross-platform publication
  selectors are rejected. Retrying the same command message is idempotent and
  publication does not enable continuous capture, Dream, or backfill.
- Reply to a whitelisted user's message with `/ai_bank_allow [source]` or
  `/ai_bank_deny [source]` to grant or revoke that exact knowledge bank for that
  platform-qualified account. An omitted source means the current group. On
  Telegram use an `@username` or message link; on QQ use a numeric group ID. A
  bank from another chat platform requires its full canonical bank ID, such as
  `qq:group:686743769`. Creating a grant requires a valid Directory Publication;
  revocation does not depend on the publication or memory service still being
  available.
  `/ai_deny` also removes all of that account's bank grants, so re-allowing the
  user starts with primary-chat memory only.
- Reply in a thread with `/ai_memory` to ingest the bounded human reply chain,
  attributing each message to its author. An accepted owner's command is deleted
  after the configured delay while its result or error acknowledgement remains
  visible. Invalid usage remains visible for correction. Add
  an instruction, such as `/ai_memory Correct their employer to Acme`, to ingest
  the chain, retain a separate owner-attributed correction Episode, and apply a
  reversible Hindsight-native Revision to the directly replied user. AI-generated
  answers and AI control commands are not retained as human evidence.
- Telegram only: forward a message to the userbot account's Saved Messages to
  ingest the original message and its bounded ancestor reply chain without
  posting a command in the source chat. When Telegram hides the forward source,
  paste the original public
  or private supergroup/channel message link as the entire Saved Messages message;
  Telefire resolves the linked message with the authenticated account and ingests
  the same bounded chain. Forum message links are supported, while channel-comment
  links with `?comment=` are ignored in v1. Forwarded copies and pasted links remain
  in Saved Messages. Telefire replies with `Remembering...` while ingestion runs,
  then edits that reply to `Remembered.` or an actionable error. Premium accounts
  also get a best-effort `✍` success or `👎` failure tag. Telegram-delivery
  duplicates are suppressed, while forwarding or pasting the source again
  intentionally retries ingestion. Other directly typed Saved Messages are not
  memory requests.
- `/ai_cancel` cancels the requester's active Agent Run.
- Every successful `/ai` request retains its bounded human reply path plus the
  current prompt. No chat-level memory switch is required. `/ai_memory_enable`
  instead enables continuous capture of all eligible settled messages arriving
  after the command; `/ai_memory_disable` stops that background capture. Both
  commands accept an optional numeric chat target, so the owner can manage
  another accessible chat from anywhere. Telegram accepts its signed
  group/channel ID, for example `/ai_memory_enable -1002064685671`; QQ accepts
  a positive group ID. Remote enable starts after that target's latest message.
- `/ai_dream_enable` separately enables scheduled Dream scans and
  `/ai_dream_disable` disables them. These commands accept the same optional
  numeric chat target. Continuous capture overrides Dream while both settings are
  enabled, so the same chat is never scanned by both workers.
  `/ai_memory_dream` runs one bounded Dream scan immediately when Dream is enabled
  and continuous capture is disabled. `/ai_memory_status` reports both modes,
  their cursor, and the latest Dream attempt, success, and failure for the current
  chat. `/ai_memory_list` reports every chat with continuous memory or Dream
  enabled, including its display name, canonical numeric ID, cursor, override
  state, and latest errors. Recall and one-shot `/ai_memory` do not depend on
  either mode. Accepted owner memory-management commands delete on the configured
  timer.
- Dream-enabled chats are scanned on `TELEFIRE_MEMORY_DREAM_CRON` (hourly by default).
  Lookback, overlap, settlement delay, concurrency, transport batch size, and
  bounded retry settings use the `TELEFIRE_MEMORY_DREAM_*` variables documented
  in `.env.example`. Continuous polling and scope concurrency use the
  `TELEFIRE_MEMORY_CONTINUOUS_*` variables. Set the Dream cron value to `off` to
  disable scheduled scans without changing per-chat settings.
- `/ai_memory_dream` manually scans the configured settled time window for a
  Dream-enabled chat. Standalone messages become one-message Episodes; replies are
  grouped by their bounded root. The fixed scan watermark advances only after all
  document updates are accepted. A window or thread over its configured bound fails
  without advancing, so the owner can narrow the lookback or raise the bound safely.
- `/ai_memory_backfill days 7` performs a one-shot scan of the rolling seven-day
  window ending at the configured settlement cutoff. `/ai_memory_backfill messages
  500` instead scans the latest 500 settled seed messages. Both forms are owner-only,
  operate on the current chat Bank, and work even when both background modes are
  disabled.
  Reply ancestors may be added as context, so retained event count can exceed the
  requested seed count. Backfill shares the per-chat Dream lease and ingestion
  pipeline but never changes the scheduled Dream watermark. The first version
  accepts 1-30 days or 1-5,000 messages; a day window containing over 5,000 messages
  fails before retention. Safely rerun the same command after interruption because
  accepted Episode documents are receipt-backed and idempotent.

Unauthorized users are ignored. Delegated users get one request in flight and
a 30-second cooldown by default. AI invocation is controlled by the owner and
per-user whitelist; there is no chat-level AI gate.

Every authorized request uses the same constrained `web_search`, `fetch_content`,
and QuickJS `code_exec` policy. Code has no host filesystem, environment, shell,
process, or network APIs, and fetched URLs cannot resolve to loopback, private, or
container-internal addresses. Tool calls execute automatically; transient tool
snapshots are replaced when the final answer begins.
When initial recall is insufficient, both owner and delegated runs may use one
bank-pinned `memory_reflect` call and bounded `memory_get_sources` calls for IDs
already returned in that run. These tools are read-only and use fixed host budgets.

Attachment analysis is bounded to the current attachment plus three attachments
from the reply chain. Files over 5 MiB are not downloaded. Images are normalized
in memory before a non-persistent Pi vision call; PDFs and text-like files are
text-extracted in memory and summarized. Audio, video, stickers, archives, and
unsupported binaries contribute metadata only. Raw attachment bytes, Telegram
download URLs, and temporary paths are never written to Pi sessions, AI state, or
memory. Only bounded generated descriptions, OCR text, captions, and safe metadata
can enter conversation context and per-user memory.

AI conversation-to-Pi mappings, access state, cooldown timestamps, capture
labels, Dream state, and ingestion receipts are stored in each adapter's
configured SQLite state path. Facts, observations, entities, source Episodes,
and relationships live only in Hindsight. Pi's append-only Agent Sessions are
stored in its own data volume. All locations contain private chat-derived data.

Back up the three active volumes while their writers are stopped, or through a
storage-aware snapshot mechanism:

```bash
docker compose --env-file .env stop ai onebot-ai
docker compose --env-file agent/.env -f agent/compose.yml stop pi-agent
docker compose --env-file memory/.env -f memory/compose.yml stop memory-api
docker run --rm -v telefire_telefire-runtime:/source:ro \
  -v "$PWD/backups":/backup alpine tar -C /source -czf /backup/telefire-runtime.tgz .
docker run --rm -v pi-agent-data:/source:ro \
  -v "$PWD/backups":/backup alpine tar -C /source -czf /backup/pi-agent-data.tgz .
docker run --rm -v memory-hindsight-data:/source:ro \
  -v "$PWD/backups":/backup alpine tar -C /source -czf /backup/memory-hindsight-data.tgz .
docker compose --env-file memory/.env -f memory/compose.yml up -d
docker compose --env-file agent/.env -f agent/compose.yml up -d
docker compose --env-file .env up -d
```

Restore into empty volumes while the stack is stopped, then recreate the stack and
check all four health endpoints before enabling Dream again. Accepted Episodes,
receipts, cursors, and expired leases are restart-safe; an interrupted retain or
Dream batch is retried from its stable document identity rather than rolled back.
Do not restore an adapter state database or Hindsight independently from backups
taken at unrelated times unless duplicate delivery is acceptable.

The retired Zvec source is preserved in the offline Docker volume
`telefire-legacy-zvec` for 30 days after cutover and is not mounted by the running
stack. The archive is intentionally managed outside Compose so recreation cannot
attach it to a runtime service. Create and verify that archive once, replacing
`<legacy-zvec-volume>` with the pre-cutover source volume:

```bash
docker volume create telefire-legacy-zvec
docker run --rm \
  -v <legacy-zvec-volume>:/source:ro \
  -v telefire-legacy-zvec:/archive \
  alpine sh -c 'cp -a /source/. /archive/'
docker run --rm -v telefire-legacy-zvec:/archive:ro \
  alpine sh -c 'test -n "$(find /archive -mindepth 1 -print -quit)"'
docker inspect telefire-ai pi-agent memory-api \
  --format '{{range .Mounts}}{{.Name}} {{end}}'
```

The final inspection must not print `telefire-legacy-zvec`. A dry run or explicit
migration from an archived source uses:

```bash
uv run --extra legacy-migration telefire-memory-migrate \
  --source /path/to/legacy-memory
```

Add `--execute --hindsight-url http://127.0.0.1:18888` only after reviewing the
report. The migration imports source Observations and labels, not derived facts,
profiles, scores, or vectors. Recoverable legacy suppressions become marked
correction Episodes and are applied through reversible Hindsight invalidation.
Every rerun verifies destination document content, so restoring or replacing the
Hindsight volume cannot be masked by surviving local receipts. Stores above 100,000
legacy records fail explicitly instead of producing a partial migration.

First-version limits: banks never search one another automatically; identity is not
merged across chats or platforms; Dream scans only explicitly enabled,
adapter-owned scopes and only a bounded window; deleted chat messages do not
retract retained evidence; raw media is not stored; revisions preserve source
history; and no hard-delete, dashboard editing, high-availability database, or
disaster-recovery automation is provided.

The loopback Hindsight API and inspection UIs trust other processes on the local
host. They are not authenticated public services and must remain bound to loopback
or be accessed through a trusted local tunnel.

If the userbot replies with `AI request failed`, inspect bounded service logs with
`docker compose --env-file .env logs ai`,
`docker compose --env-file agent/.env -f agent/compose.yml logs pi-agent`, and
`docker compose --env-file memory/.env -f memory/compose.yml logs memory-api`.
Responses and health checks do not expose API keys or raw provider payloads.

Matrix examples:

```bash
uv run telefire matrix whoami
uv run telefire matrix list_rooms
uv run telefire matrix list_rooms --account=work
uv run telefire matrix cleanup --days=30
```

Matrix E2EE examples:

```bash
uv run --extra e2ee telefire matrix crypto_status
uv run --extra e2ee telefire matrix crypto_sync --seconds=30
uv run --extra e2ee telefire matrix decrypt_history '!room:id' --limit=20
uv run --extra e2ee telefire matrix decrypt_history '!room:id' --limit=20 --request_keys=True
```

Headless verification can use the Matrix recovery/security key:

```bash
uv run --extra e2ee telefire matrix verify_recovery_key
```

After the Telefire Matrix device is cross-signed, `decrypt_history --request_keys=True` can ask the account's other Matrix devices for missing Megolm room keys and store any received sessions in `crypto.db`.
Existing encrypted history is only recoverable when another trusted device or backup still has the relevant room keys.
Do not run multiple E2EE commands for the same Matrix account concurrently; the crypto store is SQLite and should be treated as single-writer.

Long-running commands should be kept alive in `tmux`, `screen`, or a service manager:

```bash
uv run telefire telegram plus_mode
uv run telefire telegram words_to_ifttt --event=event-name --key=webhook-key outage alert
uv run telefire matrix plus_mode
```

## Architecture

The core refactor moved the project to an explicit runtime design:

- `src/telefire/runtime/command.py`
  shared sync bridge for `run_once(...)` and `run_forever(...)`
- `src/telefire/telegram/`
  Telegram config, service, store, helpers, and command wrapper
- `src/telefire/matrix/`
  Matrix config, service, store, helpers, and command wrapper
- `src/telefire/plugins/base.py`
  command registry and Fire wrapper generation

This keeps protocol runtime, storage, and command orchestration separate, while still letting plugin commands stay small.

## Notes

- TeleFire now targets Python 3.14 or newer.
- Use `uv run telefire ...` for repo-local usage.
- Protocol commands now live under `telefire telegram ...` and `telefire matrix ...`.
- Required arguments may be positional or flags, depending on the command signature shown by `--help`.
- The old "all arguments must use named flags" rule is no longer true.
- The default account is the primary setup path. Named accounts are optional manual config.
