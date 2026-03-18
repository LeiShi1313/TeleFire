---
name: telefire
description: Use when the user asks to interact with Telegram — listing chats, searching messages, fetching history, deleting messages, monitoring keywords, generating word clouds, or running any Telegram automation from the CLI
---

# Telefire

CLI tool for Telegram automation via user account. Built on Telethon + python-fire.

**Location:** `/home/lei/workspace/created/telefire/`
**Run pattern:** `python telefire.py <command> --arg1=val1 --arg2=val2`

**IMPORTANT:** All arguments must use named flags (`--chat=X`, `--user=X`). Bare positional args do NOT work with python-fire in this codebase.

## Prerequisites

Env vars in `.env` (loaded automatically):
- `TELEGRAM_API_ID`, `TELEGRAM_API_HASH` — required for all commands
- `MATRIX_BASE_URL`, `MATRIX_USER_ID`, `MATRIX_PASSWORD` — only for matrix_* commands
- `AI_API_ENDPOINT`, `AI_API_MODEL`, `AI_API_KEY` — only for ai_bot

First run prompts for phone number + Telegram code (creates session file).

## Start Here

Always run `get_all_chats` first to find chat IDs/usernames:
```bash
python telefire.py get_all_chats
```
Most commands take `--chat=` (username, numeric ID, or display name).

**`--user=` must be a Telegram username or numeric user ID.** Display names do NOT work — they fail with `ValueError: Cannot find any entity`. To find a user's username/ID from their display name, search recent messages in the chat:
```python
python -c "
import asyncio
from telethon import TelegramClient
from dotenv import load_dotenv
import os
load_dotenv()
client = TelegramClient('test', int(os.environ['TELEGRAM_API_ID']), os.environ['TELEGRAM_API_HASH'])
async def main():
    await client.start()
    chat = await client.get_entity('CHAT_NAME')
    async for msg in client.iter_messages(chat, limit=500):
        if msg.sender and hasattr(msg.sender, 'first_name'):
            name = (msg.sender.first_name or '') + (msg.sender.last_name or '')
            if 'DISPLAY_NAME' in name:
                print(f'ID: {msg.sender_id}, Name: {name}, Username: {msg.sender.username}')
                break
asyncio.run(main())
"
```

## Quick Reference

### One-Shot Commands (run and exit)

| Command | Usage | Purpose |
|---------|-------|---------|
| `get_all_chats` | `get_all_chats` | List all chats with IDs |
| `get_entity` | `get_entity --entity=X` | Resolve user/chat info |
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
| `wordcloud` | `wordcloud --redis=X` | In-chat word cloud generation (triggered by "wordcloud" message) |
| `chat_to_redis` | `chat_to_redis --redis=X` | Stream all messages to Redis |
| `lottery` | `lottery --redis=X --chat=X` | Lottery bot |

### Matrix Commands

| Command | Usage | Purpose |
|---------|-------|---------|
| `matrix_list_rooms` | `matrix_list_rooms` | List joined Matrix rooms |
| `matrix_plus_mode` | `matrix_plus_mode` | Matrix plus mode |
| `matrix_chengyu_bot` | `matrix_chengyu_bot --chat=X [--dry_run=True]` | Chinese idiom game bot |

## Common Workflows

**List messages with hourly stats:**
```bash
python telefire.py list_messages --chat=coder_ot --user=Fangliding --print_stat=True
```

**Search messages from a specific user:**
```bash
python telefire.py search_messages --chat=coder_ot --query='keyword' --user=username --limit=500
```

**Monitor keywords with phone notifications:**
```bash
# Pushbullet (direct push) — monitors ALL chats
python telefire.py words_to_pushbullet --token=TOKEN --device=DEVICE_ID outage alert

# IFTTT webhook — monitors ALL chats
python telefire.py words_to_ifttt --event=event-name --key=webhook-key outage alert

# Forward to Telegram channel — monitors SPECIFIC chats
python telefire.py words_notify --chats=chat-id keyword1 keyword2
```

**Safe message deletion (preview first):**
```bash
# Step 1: Preview with list_messages (no delete)
python telefire.py list_messages --chat=chat-name --user=your-username

# Step 2: Delete (irreversible)
python telefire.py delete_all --chat=chat-name --before='2024-01-01'
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
- Plugins that need Redis require a `--redis=` connection string argument
