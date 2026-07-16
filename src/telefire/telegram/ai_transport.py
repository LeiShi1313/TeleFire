from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import Any, Literal

from telethon.errors import FloodWaitError, MessageNotModifiedError
from telethon.extensions import html as telegram_html
from telethon.tl import functions as telegram_functions
from telethon.tl import types as telegram_types

from telefire.chat.transport import ChatPresentation, SentMessage


TelegramResponseFormat = Literal["regular_html", "rich_markdown"]
TELEGRAM_REGULAR_HTML_FORMAT_GUIDE = """Response format: Telegram regular-message HTML.
- Return only the answer. Do not wrap the whole response in a code block.
- Use only these formatting tags: <b>bold</b>, <i>italic</i>, <u>underline</u>, <s>strikethrough</s>, <code>inline code</code>, <pre>preformatted block</pre>, <blockquote>quoted text</blockquote>, and <a href="https://example.com">link text</a>.
- Use a short <b>bold heading</b> on its own line instead of Markdown headings. Use plain hyphen or numbered lines for lists.
- Telegram regular messages have no native table entity. Render a compact table as aligned text inside <pre>; render a wide table as labeled list rows.
- Escape literal <, >, and & as &lt;, &gt;, and &amp;. Close every tag. Do not nest formatting inside <code> or <pre>.
- Do not emit Markdown markers such as **, __, # headings, > quotes, backticks, or pipe-table syntax."""
TELEGRAM_RICH_MARKDOWN_FORMAT_GUIDE = """Response format: Telegram Bot API rich-message Markdown.
- Return only the answer. Use GitHub-Flavored Markdown where possible.
- Use **bold**, *italic*, ~~strikethrough~~, `inline code`, fenced code blocks, # headings, > blockquotes, lists, and links.
- Native tables use this structure:
| Header 1 | Header 2 |
|:---------|:---------|
| Value 1  | Value 2  |
- Keep tables compact, close every formatting delimiter and code fence, and do not wrap the whole response in a code block."""


def select_telegram_response_format(
    *,
    is_bot_account: bool,
    rich_messages_available: bool,
) -> TelegramResponseFormat:
    if is_bot_account and rich_messages_available:
        return "rich_markdown"
    return "regular_html"


def telegram_system_prompt(
    base_prompt: str,
    response_format: TelegramResponseFormat = "regular_html",
) -> str:
    if response_format == "regular_html":
        guide = TELEGRAM_REGULAR_HTML_FORMAT_GUIDE
    elif response_format == "rich_markdown":
        guide = TELEGRAM_RICH_MARKDOWN_FORMAT_GUIDE
    else:
        raise ValueError(f"Unsupported Telegram response format: {response_format}")
    return f"{base_prompt.rstrip()}\n\n{guide}".lstrip()


class TelegramEditLimiter:
    def __init__(
        self,
        minimum_interval: float,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] | None = None,
        logger: Any | None = None,
    ):
        self._minimum_interval = max(0.0, minimum_interval)
        self._clock = clock
        self._sleep = sleep or asyncio.sleep
        self._logger = logger
        self._lock = asyncio.Lock()
        self._next_edit_at = 0.0

    async def run(
        self,
        operation: Callable[[], Awaitable[Any]],
        *,
        wait: bool,
    ) -> bool:
        if not wait and self._lock.locked():
            return False

        async with self._lock:
            while True:
                now = self._clock()
                delay = self._next_edit_at - now
                if delay > 0:
                    if not wait:
                        return False
                    await self._sleep(delay)
                    now = self._next_edit_at
                    self._next_edit_at = 0.0

                try:
                    await operation()
                except FloodWaitError as exc:
                    seconds = max(0.0, float(exc.seconds))
                    self._next_edit_at = now + seconds
                    if self._logger is not None:
                        self._logger.warning(
                            "Telegram edit rate limited; waiting %.0f seconds",
                            seconds,
                        )
                    if not wait:
                        return False
                    continue
                except MessageNotModifiedError:
                    self._next_edit_at = now + self._minimum_interval
                    return True
                except Exception:
                    self._next_edit_at = now + self._minimum_interval
                    raise

                self._next_edit_at = now + self._minimum_interval
                return True


class TelegramChatTransport:
    def __init__(
        self,
        response_format: TelegramResponseFormat = "regular_html",
        *,
        edit_limiter: TelegramEditLimiter | None = None,
        edit_cadence: float = 4.0,
        clock: Callable[[], float] = time.monotonic,
        logger: Any | None = None,
    ):
        self._response_format = response_format
        self._edit_limiter = edit_limiter or TelegramEditLimiter(
            edit_cadence,
            clock=clock,
            logger=logger,
        )

    async def get_reply(self, message: Any) -> Any | None:
        operation = getattr(message, "get_reply_message", None)
        return await operation() if callable(operation) else None

    async def reply(
        self,
        message: Any,
        text: str,
        *,
        presentation: ChatPresentation,
    ) -> SentMessage:
        operation = getattr(message, "reply", None)
        if not callable(operation):
            raise RuntimeError("Telegram message cannot be replied to")
        return await operation(text, parse_mode=None)

    async def update(
        self,
        message: SentMessage,
        text: str,
        *,
        presentation: ChatPresentation,
        wait: bool,
    ) -> bool:
        if presentation == "plain":
            return await self._edit_limiter.run(
                lambda: message.edit(text, parse_mode=None),
                wait=wait,
            )
        if self._response_format == "rich_markdown":
            return await self._update_rich_markdown(message, text, wait=wait)
        rendered, entities = telegram_html.parse(text)
        if not rendered.strip():
            return False
        return await self._edit_limiter.run(
            lambda: message.edit(
                rendered,
                parse_mode=None,
                formatting_entities=entities,
            ),
            wait=wait,
        )

    async def delete(self, message: Any) -> None:
        operation = getattr(message, "delete", None)
        if callable(operation):
            await operation()

    def is_outgoing(self, message: Any) -> bool:
        return bool(getattr(message, "out", False))

    async def _update_rich_markdown(
        self,
        message: SentMessage,
        text: str,
        *,
        wait: bool,
    ) -> bool:
        if not text.strip().strip("*_~`#>|:-"):
            return False
        client = getattr(message, "client", None)
        get_input_chat = getattr(message, "get_input_chat", None)
        if client is None or not callable(get_input_chat):
            raise RuntimeError("Telegram rich-message editing is unavailable")
        peer = await get_input_chat()
        if peer is None:
            raise RuntimeError("Telegram rich-message peer is unavailable")

        async def edit() -> None:
            await client(
                telegram_functions.messages.EditMessageRequest(
                    peer=peer,
                    id=message.id,
                    rich_message=telegram_types.InputRichMessageMarkdown(
                        markdown=text
                    ),
                )
            )

        return await self._edit_limiter.run(edit, wait=wait)
