# TeleFire

TeleFire is a CLI for Telegram and Matrix automation.

The current codebase is `uv`-first, Python 3.14+, and built around explicit runtime layers instead of protocol logic living directly in command classes.

## Highlights

- Telegram runtime on Telethon
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

Copy the settings from `.env.example` into a private `.env` and configure an
OpenAI-compatible chat provider. Pi uses that provider for Agent Runs; the
Hindsight service uses it for memory extraction and reflection. The embedding
model and dimension define one fixed vector space; changing either requires an
explicit re-ingestion and re-embedding operation.
`TELEFIRE_AI_REASONING_EFFORT` is optional and accepts `none`, `minimal`, `low`,
`medium`, `high`, `xhigh`, or `max` when supported by the selected chat model.
`TELEFIRE_AI_EDIT_CADENCE` is the account-wide minimum interval between Telegram
message edits and defaults to 4 seconds. Intermediate stream updates are skipped
when the edit slot is busy; final answers wait for the next slot.
`TELEFIRE_MEMORY_COMMAND_DELETE_DELAY` controls how many seconds accepted owner
memory commands remain visible and defaults to 3 seconds.

For a host-only development run, start Hindsight through Compose, then the Pi
Agent Engine:

```bash
docker compose up -d hindsight
cd agent
npm ci
set -a
source ../.env
set +a
npm start
```

It binds to `127.0.0.1:8790` when `TELEFIRE_PI_HOST=127.0.0.1` is set. The
read-only operational dashboard can be started separately from the repository
root:

```bash
set -a
source .env
set +a
uv run telefire-memory-dashboard
```

It binds to `127.0.0.1:8765` by default. In another terminal, start the Telegram
userbot:

```bash
uv run telefire telegram ai
```

### Docker compose

Build and run Hindsight, Pi, the read-only dashboard, and the Telegram AI
userbot:

```bash
docker compose up -d
```

The stack uses `hindsight` as the sole memory engine, `pi` as the Agent Engine,
`memory-dashboard` as a read-only inspection surface, and `ai` as the long-running
userbot. The userbot container has no model API key. Pi receives only a host-pinned
bank and bounded references for each run; memory tools cannot select another bank
or write memory.

Pi health is published on loopback at
`http://127.0.0.1:${TELEFIRE_PI_EXPOSE_PORT:-18790}/health`. Its run API is an
authenticated internal Telefire interface rather than a public
OpenAI-compatible endpoint. Set one private `TELEFIRE_PI_TOKEN` value in
`.env`; Compose supplies it only to the Pi and AI containers.

The Telefire dashboard is available at
`http://127.0.0.1:${TELEFIRE_MEMORY_DASHBOARD_EXPOSE_PORT:-18866}/admin` and the
native Hindsight UI at
`http://127.0.0.1:${TELEFIRE_HINDSIGHT_UI_EXPOSE_PORT:-19999}`. Both published
ports bind to loopback because they contain private chat-derived evidence.

`TELEGRAM_API_ID` and `TELEGRAM_API_HASH` must be set in `.env` (or provided via
host config). Pi and Hindsight provider variables come from the same file.

A Telegram API login/session still needs to be initialized once:

```bash
docker compose run --rm -it ai telefire telegram login --account default
```

Then restart the AI service:

```bash
docker compose restart ai
```

The standalone Ollama Compose stack joins the external `ollama-embedding` network.
Point Hindsight at that service in `.env`:

```bash
TELEFIRE_AI_EMBEDDING_BASE_URL=http://ollama-embedding-ollama-1:11434/v1
TELEFIRE_AI_EMBEDDING_API_KEY=ollama
```

For a host Ollama process instead, use
`TELEFIRE_AI_EMBEDDING_BASE_URL=http://host.docker.internal:11434/v1`.
Compose maps that hostname to the Linux host gateway. In Docker,
`http://127.0.0.1:11434` is the Hindsight container itself and must not be used.

Configuration is loaded from `.env` in the repository root. `telefire-runtime`
contains `ai.db` and Telegram sessions, `telefire-hindsight` contains memory, and
Pi's private Agent Sessions live in `telefire-pi-runtime`.

Commands and reply behavior:

- `/ai <question>` starts a conversation. A replied message chain is reference
  context; only text after `/ai` is the current instruction. After a successful
  answer, human-authored messages in that bounded chain are ingested under their
  respective users. Relevant scoped memory is retrieved for the requester and
  the human reply-chain participants.
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
- Reply in a thread with `/ai_memory` to ingest the bounded human reply chain,
  attributing each message to its author. An accepted owner's command is deleted
  after the configured delay while its result or error acknowledgement remains
  visible. Invalid usage remains visible for correction. Add
  an instruction, such as `/ai_memory Correct their employer to Acme`, to ingest
  the chain, retain a separate owner-attributed correction Episode, and apply a
  reversible Hindsight-native Revision to the directly replied user. AI-generated
  answers and AI control commands are not retained as human evidence.
- Forward a message to the userbot account's Saved Messages to ingest the original
  message and its bounded ancestor reply chain without posting a command in the
  source chat. When Telegram hides the forward source, paste the original public
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
- `/ai_memory_enable` opts the current chat into automatic memory capture after
  successful AI requests. `/ai_memory_disable` stops future automatic capture,
  `/ai_memory_dream` runs one bounded scan immediately, and `/ai_memory_status`
  reports enablement plus the latest Dream attempt, success, and failure. Recall and one-shot
  `/ai_memory` remain available while automatic capture is disabled. Accepted
  owner memory-management commands delete on the same configured timer, without
  waiting for Dream or backfill work to finish.
- Enabled chats are scanned on `TELEFIRE_MEMORY_DREAM_CRON` (hourly by default).
  Lookback, overlap, settlement delay, concurrency, transport batch size, and
  bounded retry settings use the `TELEFIRE_MEMORY_DREAM_*` variables documented
  in `.env.example`. Set the cron value to `off` to disable scheduled scans while
  retaining manual Dream and post-`/ai` capture.
- `/ai_memory_dream` manually scans the configured settled time window for an
  enabled chat. Standalone messages become one-message Episodes; replies are
  grouped by their bounded root. The fixed scan watermark advances only after all
  document updates are accepted. A window or thread over its configured bound fails
  without advancing, so the owner can narrow the lookback or raise the bound safely.
- `/ai_memory_backfill days 7` performs a one-shot scan of the rolling seven-day
  window ending at the configured settlement cutoff. `/ai_memory_backfill messages
  500` instead scans the latest 500 settled seed messages. Both forms are owner-only,
  operate on the current chat Bank, and work even when automatic memory is disabled.
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

AI conversation-to-Pi mappings, access state, cooldown timestamps, capture labels,
Dream state, and ingestion receipts are stored in `~/.telefire/ai.db`. Facts,
observations, entities, source Episodes, and relationships live only in Hindsight.
Pi's append-only Agent Sessions are stored in its own data volume. All locations
contain private chat-derived data.

Back up the three active volumes while their writers are stopped, or through a
storage-aware snapshot mechanism:

```bash
docker compose stop ai pi hindsight memory-dashboard
docker run --rm -v telefire_telefire-runtime:/source:ro \
  -v "$PWD/backups":/backup alpine tar -C /source -czf /backup/telefire-runtime.tgz .
docker run --rm -v telefire_telefire-pi-runtime:/source:ro \
  -v "$PWD/backups":/backup alpine tar -C /source -czf /backup/telefire-pi-runtime.tgz .
docker run --rm -v telefire_telefire-hindsight:/source:ro \
  -v "$PWD/backups":/backup alpine tar -C /source -czf /backup/telefire-hindsight.tgz .
docker compose up -d
```

Restore into empty volumes while the stack is stopped, then recreate the stack and
check all four health endpoints before enabling Dream again. Accepted Episodes,
receipts, cursors, and expired leases are restart-safe; an interrupted retain or
Dream batch is retried from its stable document identity rather than rolled back.
Do not restore `ai.db` or Hindsight independently from backups taken at unrelated
times unless duplicate delivery is acceptable.

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
docker inspect telefire-ai telefire-pi telefire-hindsight \
  telefire-memory-dashboard --format '{{range .Mounts}}{{.Name}} {{end}}'
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
merged across chats or platforms; Dream scans only Telegram and only a bounded
window; deleted Telegram messages do not retract retained evidence; raw media is not
stored; revisions preserve source history; and no hard-delete, dashboard editing,
high-availability database, or disaster-recovery automation is provided.

The loopback Hindsight API and inspection UIs trust other processes on the local
host. They are not authenticated public services and must remain bound to loopback
or be accessed through a trusted local tunnel.

If the userbot replies with `AI request failed`, inspect bounded service logs with
`docker compose logs ai pi hindsight memory-dashboard`. Responses and health checks do not expose API
keys or raw provider payloads.

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
