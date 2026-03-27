---
name: telefire
description: Use when the user asks to interact with Telegram or Matrix through the TeleFire CLI — listing chats or rooms, resolving account info, searching messages, fetching history, deleting messages, monitoring keywords, or running Matrix room commands
---

# Telefire

TeleFire is a CLI for Telegram and Matrix automation.

Current baseline:

- Python 3.14+
- Telegram on Telethon
- Matrix on mautrix
- account-aware config in `~/.telefire/config.toml`

Install options:

- repo-local: `uv run telefire ...`
- one-shot: `uvx telefire ...`
- global install: `uv tool install telefire` or `pipx install telefire`

Run pattern:

- `telefire telegram <command> [args...]`
- `telefire matrix <command> [args...]`
- `telefire init`

## Current CLI Rules

- Protocol commands are grouped:
  - `telefire telegram ...`
  - `telefire matrix ...`
- `init` stays top-level.
- Required args can be positional or flags. Always trust the relevant `--help` output.
- The old "all arguments must use named flags" rule is no longer valid.
- Prefer `--account` for Telegram and Matrix.
- Telegram also accepts `--session` as an explicit session-file override.
- Default Telegram session name is `telefire`.

## Setup Checklist

Before running TeleFire commands, verify the runtime state:

1. Check config:

```bash
cat ~/.telefire/config.toml
```

Minimum Telegram config:

- `[telegram]` with `api_id` and `api_hash`
- `[telegram_accounts.*]` sections are optional — only needed for multi-account setups

Minimum Matrix config:

- `[matrix_accounts.<name>]` with `base_url` and `user_id`
- optional `device_name`
- and either:
  - a `password` for first-run bootstrap
  - or an existing session file under `~/.telefire/matrix/<account>/session.json`

2. Check the selected Telegram session file:

```bash
ls ~/.telefire/telegram/telefire.session
```

For other accounts, pass `--account=<name>` — it resolves the session name automatically.

3. Validate the selected Telegram session:

```bash
uv run telefire telegram get_entity me
uv run telefire telegram get_entity me --account=work
```

4. Validate a Matrix account:

```bash
uv run telefire matrix whoami
uv run telefire matrix whoami --account=work
```

If the Matrix account only has a password configured, the first successful run will bootstrap and persist a session token under `~/.telefire/matrix/<account>/`.

5. If needed, inspect the Matrix account store directly:

```bash
find ~/.telefire/matrix -maxdepth 2 -type f | sort
```

6. If a Telegram session is invalid, remove the selected session files and re-authenticate:

```bash
rm ~/.telefire/telegram/<session>.session*
```

Then rerun a Telegram command and complete the login flow.

## Cache Account and Room Data

On first use (or when stale), snapshot account details and room/chat lists so later requests can skip the API call:

```bash
# Telegram — cache identity and chat list
uv run telefire telegram get_entity me
uv run telefire telegram get_all_chats

# Matrix — cache identity and room list
uv run telefire matrix whoami
uv run telefire matrix list_rooms
```

Save the output to a scratch file or memory so you can resolve chat/room names to IDs without re-running these commands each time the user asks about a specific chat.

## Config Model

Recommended shape:

```toml
[telegram]
api_id = 1094995
api_hash = "..."
default_account = "default"
store_dir = "/home/you/.telefire/telegram"

[telegram_accounts.default]
session_name = "telefire"

[telegram_accounts.work]
session_name = "work"

[matrix_accounts.default]
base_url = "https://matrix.example.com"
user_id = "@you:example.com"
device_name = "telefire"
store_dir = "/home/you/.telefire/matrix/default"
password = "..."
```

Telegram storage:

- `~/.telefire/telegram/telefire.session`
- `~/.telefire/telegram/work.session`

Matrix storage:

- `~/.telefire/matrix/default/session.json`
- `~/.telefire/matrix/default/sync_store.json`
- `~/.telefire/matrix/default/state_store.bin`

Matrix accounts are account-scoped by directory, unlike Telegram where multiple accounts usually share one store dir and differ by session filename.

## Discovery

Run `--help` at any level:

```bash
uv run telefire --help
uv run telefire telegram --help
uv run telefire matrix --help
uv run telefire telegram COMMAND --help
```

## Quick Reference

### Telegram One-Shot Commands

| Command | Key Args | Purpose |
|---------|----------|---------|
| `get_all_chats` | | List all chats with IDs |
| `get_entity` | `ENTITY` | Resolve user/chat info (`me`, username, or chat) |
| `find_user` | `CHAT NAME [--limit=500]` | Find username/ID from display name |
| `search_messages` | `CHAT QUERY [--user=X] [--slow=True] [--limit=100] [--before=DATE] [--after=DATE]` | Search messages. `--slow=True` for Chinese text |
| `list_messages` | `CHAT [--user=X] [--print_stat=True] [--before=DATE] [--after=DATE]` | List messages. `--print_stat` for hourly distribution |
| `get_messages_by_ids` | `CHAT --ids=ID1,ID2` | Fetch specific messages by ID |
| `summary_messages` | `CHAT [--user=X] [--limit=10]` | Recent messages from user |
| `list_deleted_user_messages` | `CHAT` | Messages from deleted accounts |
| `delete_all` | `CHAT [--before=DATE] [--after=DATE] [--query=X]` | Delete own messages (**irreversible, no dry-run**) |
| `chat_count` | `CHAT [--user=X]` | Message count statistics |
| `regex_messages` | `CHAT REGEX` | Filter messages by regex |

### Matrix Commands

| Command | Key Args | Purpose |
|---------|----------|---------|
| `whoami` | | Show current Matrix identity |
| `list_rooms` | | List joined rooms |

All Telegram commands accept `--account=X` and `--session=X`. All Matrix commands accept `--account=X`.

## Useful Telegram Workflows

List chats:

```bash
uv run telefire telegram get_all_chats
```

Resolve an entity:

```bash
uv run telefire telegram get_entity me
uv run telefire telegram get_entity username_or_chat
```

Search messages:

```bash
uv run telefire telegram search_messages --chat=coder_ot --query='keyword'
uv run telefire telegram search_messages --chat=coder_ot --query='中文' --slow=True
```

List messages from a specific user:

```bash
uv run telefire telegram list_messages --chat=coder_ot --user=Fangliding --print_stat=True
```

Delete your own messages:

```bash
uv run telefire telegram list_messages --chat=chat-name --user=your-username
uv run telefire telegram delete_all --chat=chat-name --before='2024-01-01'
```

## Useful Matrix Workflows

List rooms:

```bash
uv run telefire matrix list_rooms --account=default
```

## Notes

- **`--user` requires username or numeric ID** — display names fail with `ValueError`. Use `find_user` to resolve.
- `search_messages --slow=True` is the safer choice for Chinese text (server-side search performs poorly with CJK).
- `delete_all` is **irreversible with no dry-run**. Always preview with `list_messages` first.
- `--before` and `--after` accept flexible date strings parsed by dateutil (e.g. `2024-01-01`, `last week`).
- Prefer `--account` for both Telegram and Matrix. Use `--session` only for raw Telegram session-file overrides.
- Plugins that need storage accept `--db=PATH` for custom SQLite path (defaults to `~/.telefire/data.db`).
