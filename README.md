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
memory service uses it for extraction. The embedding model and dimension define
one fixed vector space; changing either requires rebuilding the memory store.
`TELEFIRE_AI_REASONING_EFFORT` is optional and accepts `none`, `minimal`, `low`,
`medium`, `high`, `xhigh`, or `max` when supported by the selected chat model.

For a host-only development run, start the Pi Agent Engine first:

```bash
cd agent
npm ci
set -a
source ../.env
set +a
npm start
```

It binds to `127.0.0.1:8790` when `TELEFIRE_PI_HOST=127.0.0.1` is set. Then
start the standalone memory service from the repository root:

```bash
set -a
source .env
set +a
uv run telefire-memory
```

It binds to `127.0.0.1:8765` by default. In another terminal, start the
Telegram userbot:

```bash
uv run telefire telegram ai
```

### Docker compose

Build and run all three services (Pi Agent Engine, memory service, and Telegram
AI userbot):

```bash
docker compose up -d
```

The stack uses `pi` as the isolated Agent Engine, `memory` as the vector-memory
service, and `ai` as the long-running userbot. The userbot container has no model
API key, while the Pi container has no Telegram, Matrix, or memory credentials.

Pi health is published on loopback at
`http://127.0.0.1:${TELEFIRE_PI_EXPOSE_PORT:-18790}/health`. Its run API is an
authenticated internal Telefire interface rather than a public
OpenAI-compatible endpoint. Set one private `TELEFIRE_PI_TOKEN` value in
`.env`; Compose supplies it only to the Pi and AI containers.

The memory service also exposes a read-only dashboard at
`http://127.0.0.1:${TELEFIRE_MEMORY_EXPOSE_PORT:-8765}/admin`. The published
port is bound to loopback because the dashboard contains private chat-derived
profiles, observations, facts, and episodes.

`TELEGRAM_API_ID` and `TELEGRAM_API_HASH` must be set in `.env` (or provided via
host config). Pi and memory provider variables come from the same file.

A Telegram API login/session still needs to be initialized once:

```bash
docker compose run --rm -it ai telefire telegram login --account default
```

Then restart the AI service:

```bash
docker compose restart ai
```

If you run Ollama locally or via a separate container, set embedding settings in `.env`:

```bash
TELEFIRE_AI_EMBEDDING_BASE_URL=http://host.docker.internal:11434/v1
TELEFIRE_AI_EMBEDDING_API_KEY=ollama
```

In Docker, `http://127.0.0.1:11434` is the memory container itself, so it must be
remapped to a host-reachable address.

Configuration is loaded from `.env` in the repository root. `telefire-runtime`
contains `ai.db`, the memory index, and Telegram sessions. Pi transcripts and its
owner workspace are kept separately in `telefire-pi-runtime`.

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
  access. The owner is always allowed. The owner's command message is deleted
  after handling, while the acknowledgement remains visible.
- Reply in a thread with `/ai_memory` to ingest the bounded human reply chain,
  attributing each message to its author. The owner's bare command message is
  deleted after handling, while the result acknowledgement remains visible. Add
  an instruction, such as `/ai_memory Correct their employer to Acme`, to ingest
  the chain and revise only the directly replied user's profile using that chain
  as evidence. AI-generated answers and AI control commands are not ingested as
  human memory.
- Forward a message to the userbot account's Saved Messages to ingest the original
  message and its bounded ancestor reply chain without posting a command in the
  source chat. When Telegram hides the forward source, paste the original public
  or private supergroup/channel message link as the entire Saved Messages message;
  Telefire resolves the linked message with the authenticated account and ingests
  the same bounded chain. Forum message links are supported, while channel-comment
  links with `?comment=` are ignored in v1. Forwarded copies and pasted links remain
  in Saved Messages. Premium accounts get a best-effort `✍` success or `👎` failure
  tag, while non-Premium success stays silent. Failures always receive a private
  reply with a source-access or retry instruction. Telegram-delivery duplicates
  are suppressed, while forwarding or pasting the source again intentionally
  retries ingestion. Other directly typed Saved Messages are not memory requests.
- `/ai_cancel` cancels the requester's active Agent Run.

Unauthorized users are ignored. Delegated users get one request in flight and
a 30-second cooldown by default. `TELEFIRE_AI_ALLOWED_CHAT_IDS` can contain a
comma-separated numeric chat allowlist for restricted deployments.

Every authorized request can use constrained `web_search`, `fetch_content`, and
QuickJS `code_exec`. Delegated code has no host filesystem, environment, shell,
process, or network APIs. Owner requests may additionally use Pi's persistent
workspace and full read, write, edit, search, and shell tools. Tool calls execute
automatically; transient tool snapshots are replaced when the final answer begins.

Attachment analysis is bounded to the current attachment plus three attachments
from the reply chain. Files over 5 MiB are not downloaded. Images are normalized
in memory before a non-persistent Pi vision call; PDFs and text-like files are
text-extracted in memory and summarized. Audio, video, stickers, archives, and
unsupported binaries contribute metadata only. Raw attachment bytes, Telegram
download URLs, and temporary paths are never written to Pi sessions, AI state, or
memory. Only bounded generated descriptions, OCR text, captions, and safe metadata
can enter conversation context and per-user memory.

AI conversation-to-Pi mappings, access state, cooldown timestamps, and processed
Saved Messages memory-request receipts are stored in `~/.telefire/ai.db`. Zvec
observations, facts, episodes, and profiles are stored under
`~/.telefire/memory/`. Optional canonical-key-to-display-name labels resolved from
Telegram are stored separately beside the memory index and shown in the read-only
dashboard; subject and scope keys remain unchanged. Pi's append-only Agent Sessions
are stored in its own data volume. All three locations contain private chat-derived
data.

If the userbot replies with `AI request failed`, inspect bounded service logs with
`docker compose logs ai pi memory`. Responses and health checks do not expose API
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
