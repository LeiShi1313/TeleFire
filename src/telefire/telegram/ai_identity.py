from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

from telethon import helpers as telegram_helpers
from telethon import utils as telegram_utils
from telethon.tl import types as telegram_types

from telefire.ai import (
    DirectoryPublicationTarget,
    MemoryScopeTarget,
    MentionedUser,
    MessageIdentity,
    ReplyTarget,
)
from telefire.chat.identity import IdentityCodec, NamespacedIdentityCodec
from telefire.memory_directory import DirectorySource
from telefire.telegram.message_links import parse_telegram_message_link


TELEGRAM_IDENTITY_CODEC = NamespacedIdentityCodec(
    source="telegram",
    actor_kind="user",
    scope_kind="chat",
)
_TELEGRAM_CHANNEL_CODEC = NamespacedIdentityCodec(
    source="telegram",
    actor_kind="channel",
    scope_kind="chat",
)


class TelegramMessageIdentityResolver:
    def __init__(
        self,
        *,
        codec: IdentityCodec = TELEGRAM_IDENTITY_CODEC,
        logger: Any | None = None,
    ):
        self._codec = codec
        self._logger = logger

    async def resolve(self, message: ReplyTarget) -> MessageIdentity:
        sender, chat = await asyncio.gather(
            self._load_entity(message, "get_sender"),
            self._load_entity(message, "get_chat"),
        )
        is_human = isinstance(sender, telegram_types.User) and not bool(
            getattr(sender, "bot", False)
        )
        is_broadcast_channel_post = (
            isinstance(sender, telegram_types.Channel)
            and isinstance(chat, telegram_types.Channel)
            and bool(getattr(chat, "broadcast", False))
            and sender.id == chat.id
        )
        return MessageIdentity(
            subject_id=(
                self._codec.actor_id(sender.id)
                if is_human
                else _TELEGRAM_CHANNEL_CODEC.actor_id(sender.id)
                if is_broadcast_channel_post
                else None
            ),
            subject_display_name=telegram_display_name(sender),
            scope_display_name=telegram_display_name(chat),
            is_human=is_human,
        )

    async def _load_entity(self, message: ReplyTarget, method_name: str) -> Any | None:
        method = getattr(message, method_name, None)
        if not callable(method):
            return None
        try:
            return await method()
        except Exception as exc:
            if self._logger is not None:
                self._logger.debug(
                    "Telegram identity lookup failed (%s): %s",
                    type(exc).__name__,
                    exc,
                )
            return None


class TelegramMessageMentionResolver:
    def __init__(
        self,
        client: Any,
        *,
        logger: Any | None = None,
    ):
        self._client = client
        self._logger = logger

    async def resolve(self, message: ReplyTarget) -> tuple[MentionedUser, ...]:
        text = message.raw_text or ""
        surrogate_text = telegram_helpers.add_surrogate(text)
        resolved: dict[int, MentionedUser] = {}
        for entity in getattr(message, "entities", None) or ():
            candidate: Any | None = None
            if isinstance(entity, telegram_types.MessageEntityMentionName):
                candidate = entity.user_id
            elif isinstance(entity, telegram_types.MessageEntityMention):
                mention = telegram_helpers.del_surrogate(
                    surrogate_text[entity.offset : entity.offset + entity.length]
                )
                if not mention.startswith("@") or len(mention) < 2:
                    continue
                candidate = mention
            else:
                continue
            try:
                actor = await self._client.get_entity(candidate)
            except Exception as exc:
                if self._logger is not None:
                    self._logger.debug(
                        "Telegram mention lookup failed (%s): %s",
                        type(exc).__name__,
                        exc,
                    )
                continue
            user_id = getattr(actor, "id", None)
            if not isinstance(user_id, int) or user_id <= 0:
                continue
            resolved[user_id] = MentionedUser(
                user_id=user_id,
                display_name=telegram_display_name(actor),
            )
        return tuple(resolved.values())


class TelegramMemoryScopeTargetResolver:
    def __init__(self, client: Any, *, logger: Any | None = None):
        self._client = client
        self._logger = logger

    async def resolve(
        self,
        target: str,
        *,
        include_latest_message: bool = False,
    ) -> MemoryScopeTarget:
        digits = target.removeprefix("-")
        if (
            not digits
            or not digits.isascii()
            or not digits.isdecimal()
            or int(target) == 0
        ):
            raise ValueError("Telegram target must be a non-zero numeric chat ID")
        chat_id = int(target)
        try:
            entity = await self._client.get_entity(chat_id)
            if not isinstance(
                entity,
                (telegram_types.Chat, telegram_types.Channel),
            ):
                raise ValueError("Telegram target is not a group or channel")
            latest_message_id = 0
            if include_latest_message:
                messages = await self._client.get_messages(entity, limit=1)
                if messages:
                    latest_message_id = int(messages[0].id)
        except Exception as exc:
            if self._logger is not None:
                self._logger.warning(
                    "Telegram memory scope lookup failed (chat_id=%s, error=%s): %s",
                    chat_id,
                    type(exc).__name__,
                    exc,
                )
            raise ValueError("Telegram group or channel is inaccessible") from exc
        return MemoryScopeTarget(
            chat_id=telegram_utils.get_peer_id(entity),
            display_name=telegram_display_name(entity),
            latest_message_id=latest_message_id,
        )


class TelegramDirectorySourceResolver:
    def __init__(self, client: Any, *, logger: Any | None = None):
        self._client = client
        self._logger = logger

    async def resolve_publication(
        self,
        message: ReplyTarget,
        arguments: str,
    ) -> DirectoryPublicationTarget:
        selector, description = self._publication_arguments(arguments)
        if selector is not None:
            entity = await self._resolve_selector(selector)
        else:
            entity = await self._resolve_forwarded_source(message)
            if entity is None:
                entity = await self._resolve_current(message)
        return DirectoryPublicationTarget(
            source=self._directory_source(entity),
            description=description,
        )

    async def resolve_bank(
        self,
        message: ReplyTarget,
        selector: str,
    ) -> DirectorySource:
        selector = selector.strip()
        if not selector:
            return self._directory_source(await self._resolve_current(message))
        if any(character.isspace() for character in selector):
            raise ValueError("Telegram source selector must be one token")
        return self._directory_source(await self._resolve_selector(selector))

    @staticmethod
    def _publication_arguments(arguments: str) -> tuple[str | None, str]:
        arguments = arguments.strip()
        if not arguments:
            return None, ""
        first, *remainder = arguments.split(maxsplit=1)
        if first.startswith("@") or first.casefold().startswith("https://t.me/"):
            return first, remainder[0] if remainder else ""
        if first.count(":") >= 2:
            raise ValueError("Telegram publication does not accept cross-platform IDs")
        return None, arguments

    async def _resolve_selector(self, selector: str) -> Any:
        if selector.startswith("@"):
            username = selector[1:]
            if (
                not username
                or len(username) > 64
                or not username.isascii()
                or not username.replace("_", "").isalnum()
            ):
                raise ValueError("Invalid Telegram username")
            candidate: Any = selector
        elif selector.casefold().startswith("https://t.me/"):
            link = parse_telegram_message_link(selector)
            if link is None:
                raise ValueError("Invalid Telegram message link")
            candidate = (
                f"@{link.username}"
                if link.username is not None
                else telegram_types.PeerChannel(link.channel_id)
            )
        else:
            raise ValueError("Use a Telegram @username or message link")
        try:
            return await self._client.get_entity(candidate)
        except Exception as exc:
            self._log_failure(selector, exc)
            raise ValueError("Telegram source is inaccessible") from exc

    async def _resolve_current(self, message: ReplyTarget) -> Any:
        get_chat = getattr(message, "get_chat", None)
        try:
            if callable(get_chat):
                return await get_chat()
            if message.chat_id is None:
                raise ValueError("Telegram chat identity is unavailable")
            return await self._client.get_entity(message.chat_id)
        except Exception as exc:
            self._log_failure("current chat", exc)
            raise ValueError("Telegram source is inaccessible") from exc

    async def _resolve_forwarded_source(self, message: ReplyTarget) -> Any | None:
        get_reply = getattr(message, "get_reply_message", None)
        if not callable(get_reply):
            return None
        reply = await get_reply()
        forward = getattr(reply, "fwd_from", None)
        if forward is None:
            return None
        peer = getattr(forward, "saved_from_peer", None) or getattr(
            forward,
            "from_id",
            None,
        )
        if peer is None:
            raise ValueError("Forwarded message hides its source")
        try:
            return await self._client.get_entity(peer)
        except Exception as exc:
            self._log_failure("forwarded source", exc)
            raise ValueError("Forwarded Telegram source is inaccessible") from exc

    @staticmethod
    def _directory_source(entity: Any) -> DirectorySource:
        if not isinstance(entity, (telegram_types.Chat, telegram_types.Channel)):
            raise ValueError("Telegram source must be a group or channel")
        display_name = telegram_display_name(entity)
        if display_name is None:
            raise ValueError("Telegram source has no display name")
        username = getattr(entity, "username", None)
        attributes = (
            {"username": f"@{username}"}
            if isinstance(username, str) and username.strip()
            else {}
        )
        return DirectorySource(
            bank_id=TELEGRAM_IDENTITY_CODEC.scope_id(
                telegram_utils.get_peer_id(entity)
            ),
            display_name=display_name,
            platform="telegram",
            source_kind=(
                "channel"
                if isinstance(entity, telegram_types.Channel)
                and bool(getattr(entity, "broadcast", False))
                else "group"
            ),
            attributes=attributes,
        )

    def _log_failure(self, selector: str, exc: Exception) -> None:
        if self._logger is not None:
            self._logger.warning(
                "Telegram directory lookup failed (selector=%s, error=%s): %s",
                selector,
                type(exc).__name__,
                exc,
            )


def telegram_memory_event_metadata(
    message: ReplyTarget,
    *,
    codec: IdentityCodec = TELEGRAM_IDENTITY_CODEC,
) -> dict[str, Any]:
    metadata: dict[str, Any] = {}
    chat_id = getattr(message, "chat_id", None)
    post_author = getattr(message, "post_author", None)
    if isinstance(post_author, str) and post_author.strip():
        metadata["post_author"] = post_author.strip()[:256]
    reply = getattr(message, "reply_to", None)
    quote_text = getattr(reply, "quote_text", None)
    if isinstance(quote_text, str) and quote_text.strip():
        quotation: dict[str, Any] = {"text": quote_text.strip()[:4_000]}
        reply_id = getattr(message, "reply_to_msg_id", None)
        if isinstance(chat_id, int) and isinstance(reply_id, int):
            quotation["source_id"] = codec.message_source_id(chat_id, reply_id)
        quote_offset = getattr(reply, "quote_offset", None)
        if isinstance(quote_offset, int) and quote_offset >= 0:
            quotation["offset"] = quote_offset
        metadata["quotation"] = quotation

    forward = getattr(message, "fwd_from", None)
    if forward is not None:
        attribution: dict[str, Any] = {}
        from_name = getattr(forward, "from_name", None)
        if isinstance(from_name, str) and from_name.strip():
            attribution["actor_display_name"] = from_name.strip()[:256]
        from_id = getattr(forward, "from_id", None)
        if isinstance(from_id, telegram_types.PeerUser):
            attribution["actor_id"] = codec.actor_id(from_id.user_id)
        source_peer = getattr(forward, "saved_from_peer", None) or from_id
        source_message_id = getattr(forward, "saved_from_msg_id", None) or getattr(
            forward,
            "channel_post",
            None,
        )
        if source_peer is not None and isinstance(source_message_id, int):
            try:
                source_chat_id = telegram_utils.get_peer_id(source_peer)
            except (TypeError, ValueError):
                source_chat_id = None
            if isinstance(source_chat_id, int):
                attribution["source_id"] = codec.message_source_id(
                    source_chat_id,
                    source_message_id,
                )
        forwarded_at = getattr(forward, "date", None)
        if isinstance(forwarded_at, datetime):
            if forwarded_at.tzinfo is None:
                forwarded_at = forwarded_at.replace(tzinfo=UTC)
            attribution["source_time"] = (
                forwarded_at.astimezone(UTC).isoformat().replace("+00:00", "Z")
            )
        if attribution:
            metadata["forwarded_from"] = attribution
    return metadata


def telegram_display_name(entity: Any | None) -> str | None:
    if entity is None:
        return None
    display_name = telegram_utils.get_display_name(entity).strip()
    if not display_name:
        username = (getattr(entity, "username", None) or "").strip()
        display_name = f"@{username}" if username else ""
    display_name = " ".join(display_name.split())
    return display_name[:256] or None
