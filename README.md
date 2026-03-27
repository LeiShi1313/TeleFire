# TeleFire

TeleFire is a CLI for Telegram and Matrix automation.

The current codebase is `uv`-first, Python 3.14+, and built around explicit runtime layers instead of protocol logic living directly in command classes.

## Highlights

- Telegram runtime on Telethon
- Matrix runtime on mautrix
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
uv run telefire matrix whoami --account=default
```

The first Telegram command will prompt for login if the selected session file does not exist yet.

The first Matrix command can bootstrap from the configured password, then persist `access_token` and `device_id` into the account store and reuse that session on later runs.

## Config

The default account lives directly under `[telegram]` / `[matrix]`. Additional accounts are sub-tables.

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

[matrix.work]
base_url = "https://matrix.work.example"
user_id = "@you:work.example"
device_name = "telefire"
store_dir = "/home/you/.telefire/matrix/work"
password = "..."
```

Notes:

- Telegram uses `--account` to resolve a configured session alias.
- Telegram also accepts `--session` as a low-level override.
- Matrix uses `--account` to select both config and store directory.

## Storage Layout

Telegram:

- `~/.telefire/telegram/telefire.session`
- `~/.telefire/telegram/work.session`

Matrix:

- `~/.telefire/matrix/default/session.json`
- `~/.telefire/matrix/default/sync_store.json`
- `~/.telefire/matrix/default/state_store.bin`
- `~/.telefire/matrix/work/session.json`

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
uv run telefire telegram get_entity me --account=default
uv run telefire telegram get_entity me --session=work
uv run telefire telegram get_all_chats
uv run telefire telegram list_messages --chat=coder_ot --user=Fangliding
uv run telefire telegram search_messages --chat=coder_ot --query='keyword'
```

Matrix examples:

```bash
uv run telefire matrix whoami --account=default
uv run telefire matrix list_rooms --account=default
uv run telefire matrix list_rooms --account=work
uv run telefire matrix cleanup --account=default --days=30
```

Long-running commands should be kept alive in `tmux`, `screen`, or a service manager:

```bash
uv run telefire telegram plus_mode
uv run telefire telegram words_to_ifttt --event=event-name --key=webhook-key outage alert
uv run telefire matrix plus_mode --account=default
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
