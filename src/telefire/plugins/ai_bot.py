import asyncio
import json
import traceback
import re
from typing import List, Dict, Optional
from pathlib import Path
from datetime import datetime

import aiohttp
from telethon import utils
from telethon.sync import events
from telethon.tl.types import Message

from telefire.plugins.base import Telegram, PluginMount
from loguru import logger


class AIBot(Telegram, metaclass=PluginMount):
    command_name = "ai_bot"

    API_ENDPOINT = "http://192.168.1.15:6005/v1/chat/completions"
    MAX_CONTEXT_MESSAGES = 20

    def _setup_loguru(self):
        """Configure loguru sink for conversation logging."""
        log_dir = Path("logs") / "ai_bot"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_file = log_dir / f"{datetime.utcnow().strftime('%Y-%m-%d')}.log"
        # Avoid duplicate sinks if reloads happen
        for sink in list(logger._core.handlers.keys()):
            # crude check to avoid piling up multiple same sinks
            pass
        logger.add(log_file.as_posix(), enqueue=True, rotation="00:00", retention=7, encoding="utf-8")

    async def _collect_reply_chain(self, msg: Message) -> List[Message]:
        """Walk up the reply chain starting from msg.get_reply_message()."""
        chain: List[Message] = []
        current = await msg.get_reply_message()
        visited = set()
        while current is not None and len(chain) < self.MAX_CONTEXT_MESSAGES:
            # Prevent accidental loops
            if current.id in visited:
                break
            visited.add(current.id)
            chain.append(current)
            try:
                if not current.is_reply:
                    break
                current = await current.get_reply_message()
            except Exception:
                break
        chain.reverse()
        return chain

    async def _build_messages(self, chain: List[Message], me_id: int, extra_instruction: Optional[str]) -> List[Dict]:
        messages: List[Dict] = []
        # System instruction
        system_content = "You are a helpful assistant. Answer concisely."
        if extra_instruction:
            system_content += f"\nFollow additional instruction: {extra_instruction.strip()}"
        messages.append({"role": "system", "content": system_content})

        for m in chain:
            try:
                sender = await m.get_sender()
                role = "assistant" if (sender and sender.id == me_id) else "user"
                text = m.message or ""
                # Skip empty
                if not text:
                    continue
                messages.append({
                    "role": role,
                    "content": text,
                })
            except Exception:
                continue
        return messages

    def _strip_ai_prefix(self, text: str) -> str:
        """Remove leading '/ai' and surrounding whitespace from a command message."""
        if not text:
            return ""
        # Support variants like '/ai', '/ai ', '/ai\n', '/ai    something'
        if text.startswith("/ai"):
            rest = text[3:]
            return rest.lstrip(" \t\n\r")
        return text

    def _mentions_me(self, text: str) -> bool:
        if not text or not getattr(self.me, 'username', None):
            return False
        return f"@{self.me.username.lower()}" in text.lower()

    def _strip_my_mention(self, text: str) -> str:
        if not text or not getattr(self.me, 'username', None):
            return text
        try:
            pattern = re.compile(rf"@{re.escape(self.me.username)}", re.IGNORECASE)
            return pattern.sub("", text).strip()
        except Exception:
            return text

    async def _call_api(self, messages: List[Dict]) -> str:
        """Call the configured AI API using OpenAI Chat Completions style."""
        import os
        model = os.environ.get("AI_API_MODEL", "gpt-4o-mini")
        payload = {
            "model": model,
            "messages": messages,
            "temperature": 0.7,
        }
        # Allow small timeout so we don't hang forever
        timeout = aiohttp.ClientTimeout(total=120)
        headers = {}
        # Optional bearer token support if provided
        token = os.environ.get("AI_API_KEY")
        if token:
            headers["Authorization"] = f"Bearer {token}"

        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(self.API_ENDPOINT, json=payload, headers=headers) as resp:
                text = await resp.text()
                if resp.status != 200:
                    raise RuntimeError(f"API error {resp.status}: {text}")
                try:
                    data = json.loads(text)
                except Exception:
                    # Not JSON, just return raw text
                    return text

                # Try OpenAI-compatible shapes first
                if isinstance(data, dict):
                    # explicit error message
                    if "error" in data and isinstance(data["error"], str):
                        raise RuntimeError(data["error"]) 
                    # choices[0].message.content
                    try:
                        return data["choices"][0]["message"]["content"]
                    except Exception:
                        pass
                    # content / response / message
                    for key in ("content", "response", "message", "text"):
                        if key in data and isinstance(data[key], str):
                            return data[key]
                # Fallback to string conversion
                return text

    def __call__(self):
        @self._client.on(events.NewMessage(incoming=True))
        async def _incoming(evt):
            msg: Message = evt.message
            try:
                if not msg or not msg.text:
                    return
                # Determine reply-to-me status
                is_reply_to_me = False
                replied: Optional[Message] = None
                if msg.is_reply:
                    replied = await msg.get_reply_message()
                    if replied:
                        replied_sender = await replied.get_sender()
                        is_reply_to_me = bool(replied_sender and replied_sender.id == self.me.id)

                mentions_me = self._mentions_me(msg.text)

                # Gather context from reply chain if replying
                chain: List[Message] = []
                root_msg: Optional[Message] = None
                root_has_ai = False
                if is_reply_to_me:
                    chain = await self._collect_reply_chain(msg)
                    root_msg = chain[0] if chain else None
                    root_has_ai = bool(root_msg and root_msg.text and root_msg.text.startswith("/ai"))

                # Determine trigger and user input
                trigger = False
                user_input = None

                if msg.text.startswith("/ai") and (is_reply_to_me or mentions_me):
                    trigger = True
                    # Remove '/ai' and also strip own mention if present
                    user_input = self._strip_my_mention(self._strip_ai_prefix(msg.text))
                elif is_reply_to_me and root_has_ai:
                    trigger = True
                    # Continuation: use raw text, but strip own mention if present
                    user_input = self._strip_my_mention(msg.text)

                if not trigger:
                    return

                # Build messages
                messages = await self._build_messages([], self.me.id, None)  # start with system
                if is_reply_to_me:
                    for i, m in enumerate(chain):
                        try:
                            sender = await m.get_sender()
                            role = "assistant" if (sender and sender.id == self.me.id) else "user"
                            content = m.message or ""
                            if i == 0 and root_has_ai:
                                content = self._strip_ai_prefix(content)
                            if content:
                                messages.append({"role": role, "content": content})
                        except Exception:
                            continue

                if user_input:
                    messages.append({"role": "user", "content": user_input})

                # Log conversation context
                try:
                    to_chat = await evt.get_chat()
                    chat_name = getattr(to_chat, 'title', None) or getattr(to_chat, 'username', None) or str(getattr(to_chat, 'id', 'unknown'))
                    sender = await msg.get_sender()
                    sender_name = utils.get_display_name(sender) if sender else "Unknown"
                    logger.info(f"/ai triggered in '{chat_name}' by {sender_name}")
                    logger.info("Context messages:")
                    for i, cm in enumerate(messages):
                        role = cm.get("role")
                        content = cm.get("content", "")
                        logger.info(f"[{i:02d}] {role}: {content}")
                except Exception:
                    pass

                # Indicate we are working
                try:
                    await self._client.send_message(msg.chat_id, "Thinking…", reply_to=msg.id)
                except Exception:
                    pass

                # Call AI and send result
                result = await self._call_api(messages)
                try:
                    logger.info(f"AI response: {result}")
                except Exception:
                    pass
                if not result:
                    result = "(No response)"
                await self._client.send_message(msg.chat_id, result, reply_to=msg.id)
            except Exception:
                logger.exception("AI bot error")
                try:
                    await self._client.send_message(msg.chat_id, "AI error. Check logs.", reply_to=msg.id)
                except Exception:
                    pass

        async def prepare():
            self.me = await self._client.get_me()
            self._logger.info(f"AI bot is running as {self.me.username}")
            self._setup_loguru()

        self._set_file_handler("ai_bot")
        with self._client:
            self._client.loop.run_until_complete(prepare())
        self._client.start()
        self._client.run_until_disconnected()
