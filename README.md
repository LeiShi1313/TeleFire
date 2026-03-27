# TeleFire

TeleFire is a CLI for Telegram automation and Matrix automation.

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

## Setup

Telegram requires a user API ID and API hash from https://my.telegram.org.

Run the interactive setup:

```bash
uv run telefire init
```

That writes `~/.telefire/config.toml`.

The first Telegram command will prompt for login if the selected session file does not exist yet.

The first Matrix command can bootstrap from the configured password, then persist `access_token` and `device_id` into the account store and reuse that session on later runs.

## Config

Current config is account-based.

```toml
[telegram]
api_id = 123456
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

[matrix_accounts.work]
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
- Legacy single-account config is still read for the default account, but new config should use `telegram_accounts` and `matrix_accounts`.

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
uv run telefire COMMAND --help
```

Telegram examples:

```bash
uv run telefire get_entity me
uv run telefire get_entity me --account=default
uv run telefire get_entity me --session=work
uv run telefire get_all_chats
uv run telefire list_messages --chat=coder_ot --user=Fangliding
uv run telefire search_messages --chat=coder_ot --query='keyword'
```

Matrix examples:

```bash
uv run telefire matrix_whoami --account=default
uv run telefire matrix_list_rooms --account=default
uv run telefire matrix_list_rooms --account=work
uv run telefire matrix_cleanup --account=default --days=30
```

Long-running commands should be kept alive in `tmux`, `screen`, or a service manager:

```bash
uv run telefire plus_mode
uv run telefire words_to_ifttt --event=event-name --key=webhook-key outage alert
uv run telefire matrix_plus_mode --account=default
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
- Required arguments may be positional or flags, depending on the command signature shown by `--help`.
