from __future__ import annotations

from datetime import datetime
from typing import Any

from telethon.errors import FloodWaitError

from telefire.ai import ReplyTarget, _message_datetime


class TelegramHistorySource:
    def __init__(self, client: Any):
        self._client = client

    async def fetch_recent(
        self,
        trigger: ReplyTarget,
        *,
        limit: int,
    ) -> tuple[ReplyTarget, ...]:
        if trigger.chat_id is None:
            return ()
        kwargs: dict[str, Any] = {
            "limit": limit,
            "max_id": trigger.id,
        }
        reply_header = getattr(trigger, "reply_to", None)
        if bool(getattr(reply_header, "forum_topic", False)):
            topic_id = getattr(reply_header, "reply_to_top_id", None) or getattr(
                reply_header,
                "reply_to_msg_id",
                None,
            )
            if isinstance(topic_id, int) and topic_id > 0:
                kwargs["reply_to"] = topic_id
        messages = [
            message
            async for message in self._client.iter_messages(
                trigger.chat_id,
                **kwargs,
            )
        ]
        messages.reverse()
        return tuple(messages)

    async def fetch_window(
        self,
        chat_id: int,
        *,
        since: datetime,
        until: datetime,
        limit: int,
    ) -> tuple[ReplyTarget, ...]:
        messages: list[ReplyTarget] = []
        async for message in self._client.iter_messages(
            chat_id,
            offset_date=until,
            limit=limit,
        ):
            occurred_at = _message_datetime(message)
            if occurred_at < since:
                break
            if occurred_at <= until:
                messages.append(message)
        messages.reverse()
        return tuple(messages)

    async def fetch_message(
        self,
        chat_id: int,
        message_id: int,
    ) -> ReplyTarget | None:
        message = await self._client.get_messages(chat_id, ids=message_id)
        if isinstance(message, list):
            return message[0] if message else None
        return message

    async def fetch_after(
        self,
        chat_id: int,
        *,
        after_message_id: int,
        until: datetime,
        limit: int,
    ) -> tuple[ReplyTarget, ...]:
        messages: list[ReplyTarget] = []
        async for message in self._client.iter_messages(
            chat_id,
            min_id=after_message_id,
            reverse=True,
            limit=limit,
        ):
            if _message_datetime(message) > until:
                break
            messages.append(message)
        return tuple(messages)


def telegram_source_retry_delay(exc: Exception) -> float | None:
    if isinstance(exc, FloodWaitError):
        return max(0.0, float(exc.seconds))
    return None


def telegram_channel_album_document_id(
    chat_id: int,
    message: ReplyTarget,
) -> str | None:
    if not bool(getattr(message, "post", False)):
        return None
    grouped_id = getattr(message, "grouped_id", None)
    if isinstance(grouped_id, int):
        return f"telegram:channel-album:{chat_id}:{grouped_id}"
    return None
