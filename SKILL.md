---
name: telefire
description: Use when the user asks to interact with Telegram — listing chats, searching messages, fetching history, deleting messages, monitoring keywords, generating word clouds, or running any Telegram automation from the CLI
---

# Telefire

CLI tool for Telegram automation via user account. Built on Telethon + python-fire.

**Install:** `uv tool install telefire` or `pip install telefire`
**Run pattern:** `telefire <command> --arg1=val1 --arg2=val2`

**IMPORTANT:** All arguments must use named flags (`--chat=X`, `--user=X`). Bare positional args do NOT work with python-fire in this codebase.

## Prerequisites

First-time setup:
```bash
telefire init    # saves credentials to ~/.telefire/config.toml
```
This prompts for Telegram API ID/Hash (required) and Matrix credentials (optional).
Credentials can also be set via env vars (`TELEGRAM_API_ID`, `TELEGRAM_API_HASH`) or `.env` file.

First run of any Telegram command prompts for phone number + code (creates session file).

## Start Here

Always run `get_all_chats` first to find chat IDs/usernames:
```bash
telefire get_all_chats
```
Most commands take `--chat=` (username, numeric ID, or display name).

**`--user=` must be a Telegram username or numeric user ID.** Display names do NOT work — they fail with `ValueError: Cannot find any entity`. Use `find_user` to resolve:
```bash
telefire find_user --chat=coder_ot --name='风扇'
# Output: ID: 567376438, Name: 风扇滑翔翼, Username: Fangliding
```

## Quick Reference

### One-Shot Commands (run and exit)

| Command | Usage | Purpose |
|---------|-------|---------|
| `get_all_chats` | `get_all_chats` | List all chats with IDs |
| `get_entity` | `get_entity --entity=X` | Resolve user/chat info |
| `find_user` | `find_user --chat=X --name=X [--limit=500]` | Find username/ID from display name |
| `search_messages` | `search_messages --chat=X --query=X [--user=X] [--slow=True] [--limit=100] [--before=DATE] [--after=DATE]` | Search messages. Fast=server-side (default), slow=full scan |
| `list_messages` | `list_messages --chat=X [--user=X] [--output=log] [--print_stat=True] [--cut=True] [--before=DATE] [--after=DATE]` | List all messages from a user. `--print_stat` shows hourly distribution, `--cut` enables jieba segmentation |
| `get_messages_by_ids` | `get_messages_by_ids --chat=X --ids=ID1,ID2` | Fetch specific messages |
| `summary_messages` | `summary_messages --chat=X [--user=X] [--limit=10]` | Recent messages from user |
| `list_deleted_user_messages` | `list_deleted_user_messages --chat=X` | Messages from deleted accounts |
| `delete_all` | `delete_all --chat=X [--before=DATE] [--after=DATE] [--query=X]` | Delete own messages (irreversible, no dry-run) |
| `word_cloud` | `word_cloud --chat=X [--user=X] [--start=DATE] [--end=DATE]` | Generate word cloud image |

### Long-Running Commands (event listeners, run in tmux/screen)

| Command | Usage | Purpose |
|---------|-------|---------|
| `plus_mode` | `plus_mode` | Advanced mode: auto-delete, markdown, search, AI summary |
| `auto_reply` | `auto_reply --regex=X --reply=X [--chat=X] [--from_sender=X]` | Auto-reply on regex match |
| `auto_repeat` | `auto_repeat --chat=X` | Repeat when 2 users send same text |
| `auto_reaction` | `auto_reaction --chat=X --user=X` | Auto-react to user's messages |
| `words_to_ifttt` | `words_to_ifttt --event=X --key=X WORD1 [WORD2]...` | IFTTT notification on keyword (all chats) |
| `words_to_pushbullet` | `words_to_pushbullet --token=X --device=X WORD1 [WORD2]...` | Pushbullet notification on keyword (all chats) |
| `words_notify` | `words_notify --chats=X WORD1 [WORD2]...` | Forward keyword matches to debug channel (specific chats) |
| `special_attention_mode` | `special_attention_mode --event=X --key=X PERSON1...` | IFTTT notification when specific people speak |
| `log_chat` | `log_chat` | Log all incoming messages |
| `ai_bot` | `ai_bot` | AI chatbot (responds to /ai or reply chains) |
| `wordcloud` | `wordcloud [--db=PATH]` | In-chat word cloud generation (triggered by "wordcloud" message) |
| `chat_to_redis` | `chat_to_redis [--db=PATH]` | Stream all messages to SQLite |

### Matrix Commands

| Command | Usage | Purpose |
|---------|-------|---------|
| `matrix_list_rooms` | `matrix_list_rooms` | List joined Matrix rooms |
| `matrix_plus_mode` | `matrix_plus_mode` | Matrix plus mode |
| `matrix_chengyu_bot` | `matrix_chengyu_bot --chat=X [--dry_run=True]` | Chinese idiom game bot |

## Common Workflows

**List messages with hourly stats:**
```bash
telefire list_messages --chat=coder_ot --user=Fangliding --print_stat=True
```

**Search messages from a specific user:**
```bash
telefire search_messages --chat=coder_ot --query='keyword' --user=username --limit=500
```

**Monitor keywords with phone notifications:**
```bash
# Pushbullet (direct push) — monitors ALL chats
telefire words_to_pushbullet --token=TOKEN --device=DEVICE_ID outage alert

# IFTTT webhook — monitors ALL chats
telefire words_to_ifttt --event=event-name --key=webhook-key outage alert

# Forward to Telegram channel — monitors SPECIFIC chats
telefire words_notify --chats=chat-id keyword1 keyword2
```

**Safe message deletion (preview first):**
```bash
# Step 1: Preview with list_messages (no delete)
telefire list_messages --chat=chat-name --user=your-username

# Step 2: Delete (irreversible)
telefire delete_all --chat=chat-name --before='2024-01-01'
```

**Plus mode sub-commands** (send as Telegram messages while plus_mode is running):
- `/Ns MESSAGE` — auto-delete after N seconds (e.g., `/30s hello`)
- `/Nm`, `/Nh`, `/Nd` — minutes, hours, days
- `/md` — markdown mode
- `/search user=X chat=Y query=Z` — search and create results channel
- `/summary user=X count=N [prompt]` — AI summarize recent messages
- `/getid` — get user ID (reply to their message)
- `-paolu` — delete all own messages in current chat

## Known Limitations

- **`--user=` requires username or numeric ID** — display names fail. Use the lookup snippet above to resolve.
- `delete_all` has **no dry-run mode** — always preview with `list_messages` first
- `words_to_ifttt` and `words_to_pushbullet` monitor **all chats** — no chat filter (use `words_notify` for specific chats, but it forwards to Telegram, not phone)
- Log output does **not include message dates** (date line is commented out in base.py)
- `search_messages` fast mode uses Telegram's server-side `SearchRequest` — **performs poorly with Chinese text**. Use `--slow=True` for Chinese searches (iterates all messages client-side with substring match)
- `list_messages` and `search_messages` support `--before` and `--after` date filters (parsed by dateutil, e.g. `2024-01-01`, `last week`)
- Plugins that need storage accept `--db=PATH` for custom SQLite path (defaults to `~/.telefire/data.db`)
