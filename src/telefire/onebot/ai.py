from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote

from telefire.ai import (
    MemoryScopeTarget,
    MentionedUser,
    MessageIdentity,
    ReplyTarget,
)
from telefire.chat.identity import ExternalId, IdentityCodec
from telefire.chat.transport import ChatPresentation, SentMessage
from telefire.onebot.client import OneBotActionError
from telefire.onebot.message import (
    OneBotActionClient,
    OneBotMessage,
    OneBotMessageError,
)


ONEBOT_PLAIN_TEXT_FORMAT_GUIDE = """Response format: QQ plain text.
- Return only the answer.
- QQ messages do not support reliable Markdown or HTML formatting. Do not emit Markdown markers, HTML tags, pipe tables, or fenced code blocks.
- Use short plain-text headings, numbered lines, hyphen lists, and indented text when structure is useful.
- Keep links as full URLs and keep tables narrow enough to read as plain aligned text."""


def onebot_system_prompt(base_prompt: str) -> str:
    return f"{base_prompt.rstrip()}\n\n{ONEBOT_PLAIN_TEXT_FORMAT_GUIDE}".lstrip()


@dataclass(frozen=True, slots=True)
class QQIdentityCodec:
    source: str = "qq"

    def actor_id(self, actor_id: ExternalId) -> str:
        return f"qq:user:{_positive_component(actor_id)}"

    def scope_id(self, scope_id: ExternalId) -> str:
        value = _signed_scope(scope_id)
        if value > 0:
            return f"qq:group:{value}"
        return f"qq:private:{abs(value)}"

    def parse_scope_id(self, scope_id: str) -> ExternalId | None:
        for prefix, sign in (("qq:group:", 1), ("qq:private:", -1)):
            if not scope_id.startswith(prefix):
                continue
            component = scope_id.removeprefix(prefix)
            if component.isascii() and component.isdecimal() and int(component) > 0:
                return sign * int(component)
            return None
        return None

    def message_source_id(
        self,
        scope_id: ExternalId,
        message_id: ExternalId,
    ) -> str:
        scope_kind, scope_value = _scope_components(scope_id)
        return (
            f"qq:message:{scope_kind}:{scope_value}:"
            f"{_component(message_id)}"
        )

    def thread_document_id(
        self,
        scope_id: ExternalId,
        root_message_id: ExternalId,
    ) -> str:
        scope_kind, scope_value = _scope_components(scope_id)
        return (
            f"qq:thread:{scope_kind}:{scope_value}:"
            f"{_component(root_message_id)}"
        )

    def revision_document_id(
        self,
        scope_id: ExternalId,
        message_id: ExternalId,
    ) -> str:
        scope_kind, scope_value = _scope_components(scope_id)
        return (
            f"qq:revision:{scope_kind}:{scope_value}:"
            f"{_component(message_id)}"
        )


QQ_IDENTITY_CODEC: IdentityCodec = QQIdentityCodec()


class OneBotDirectory:
    def __init__(self):
        self._users: dict[int, str] = {}
        self._groups: dict[int, str] = {}

    async def refresh(self, client: OneBotActionClient) -> None:
        friends, groups = await _gather_directory(client)
        self._users = _directory_labels(
            friends,
            id_field="user_id",
            label_fields=("remark", "nickname"),
        )
        self._groups = _directory_labels(
            groups,
            id_field="group_id",
            label_fields=("group_name",),
        )

    def user_name(self, user_id: int) -> str | None:
        return self._users.get(user_id)

    def scope_name(self, chat_id: int) -> str | None:
        if chat_id > 0:
            return self._groups.get(chat_id)
        return self._users.get(abs(chat_id))


@dataclass(slots=True)
class OneBotSentMessage:
    id: int
    text: str | None
    trigger: OneBotMessage


class OneBotChatTransport:
    def __init__(self, client: OneBotActionClient, *, logger: Any | None = None):
        self._client = client
        self._logger = logger

    async def get_reply(self, message: Any) -> OneBotMessage | None:
        reply_id = getattr(message, "reply_to_msg_id", None)
        if not isinstance(reply_id, int):
            return None
        try:
            payload = await self._client.call(
                "get_msg",
                {"message_id": str(reply_id)},
            )
            if not isinstance(payload, dict):
                return None
            return OneBotMessage.from_payload(
                payload,
                action_client=self._client,
                scope_display_name=getattr(message, "scope_display_name", None),
                private_peer_id=(
                    abs(message.chat_id)
                    if getattr(message, "chat_id", 0) < 0
                    else None
                ),
            )
        except (OneBotActionError, OneBotMessageError) as exc:
            self._log(
                "debug",
                "OneBot reply lookup failed (%s): %s",
                type(exc).__name__,
                exc,
            )
            return None

    async def reply(
        self,
        message: Any,
        text: str,
        *,
        presentation: ChatPresentation,
    ) -> SentMessage:
        if not isinstance(message, OneBotMessage):
            raise RuntimeError("OneBot transport requires a OneBot message")
        message_id = await self._send(message, text)
        return OneBotSentMessage(
            id=message_id,
            text=text,
            trigger=message,
        )

    async def update(
        self,
        message: SentMessage,
        text: str,
        *,
        presentation: ChatPresentation,
        wait: bool,
    ) -> bool:
        if not isinstance(message, OneBotSentMessage):
            raise RuntimeError("OneBot transport requires a OneBot sent message")
        if not wait:
            return False
        final_message_id = await self._send(message.trigger, text)
        placeholder_id = message.id
        message.id = final_message_id
        message.text = text
        try:
            await self._client.call(
                "delete_msg",
                {"message_id": str(placeholder_id)},
            )
        except OneBotActionError as exc:
            self._log(
                "warning",
                "OneBot placeholder recall failed (%s): %s",
                type(exc).__name__,
                exc,
            )
        return True

    async def delete(self, message: Any) -> None:
        message_id = getattr(message, "id", None)
        if isinstance(message_id, int):
            await self._client.call(
                "delete_msg",
                {"message_id": str(message_id)},
            )

    def is_outgoing(self, message: Any) -> bool:
        return bool(
            getattr(message, "is_outgoing", getattr(message, "out", False))
        )

    async def _send(self, trigger: OneBotMessage, text: str) -> int:
        message = [
            {"type": "reply", "data": {"id": str(trigger.id)}},
            {"type": "text", "data": {"text": text}},
        ]
        if trigger.chat_id > 0:
            action = "send_group_msg"
            params = {"group_id": str(trigger.chat_id), "message": message}
        else:
            action = "send_private_msg"
            params = {"user_id": str(abs(trigger.chat_id)), "message": message}
        response = await self._client.call(action, params)
        if not isinstance(response, dict):
            raise RuntimeError("OneBot send response is malformed")
        message_id = response.get("message_id")
        if isinstance(message_id, bool) or not isinstance(message_id, int):
            raise RuntimeError("OneBot send response has no message ID")
        return message_id

    def _log(self, level: str, message: str, *args: Any) -> None:
        operation = getattr(self._logger, level, None)
        if callable(operation):
            operation(message, *args)


class OneBotHistorySource:
    def __init__(
        self,
        client: OneBotActionClient,
        *,
        directory: OneBotDirectory | None = None,
    ):
        self._client = client
        self._directory = directory

    async def fetch_recent(
        self,
        trigger: ReplyTarget,
        *,
        limit: int,
    ) -> tuple[OneBotMessage, ...]:
        if trigger.chat_id is None:
            return ()
        messages = await self._history(
            trigger.chat_id,
            count=limit + 1,
            message_seq=trigger.id,
            scope_display_name=getattr(trigger, "scope_display_name", None),
        )
        trigger_index = next(
            (index for index, message in enumerate(messages) if message.id == trigger.id),
            None,
        )
        if trigger_index is not None:
            candidates = messages[:trigger_index]
        else:
            occurred_at = _message_time(trigger)
            candidates = tuple(
                message
                for message in messages
                if message.id != trigger.id and message.date <= occurred_at
            )
        return candidates[-limit:]

    async def fetch_window(
        self,
        chat_id: int,
        *,
        since: datetime,
        until: datetime,
        limit: int,
    ) -> tuple[OneBotMessage, ...]:
        messages = await self._history(chat_id, count=limit)
        return tuple(
            message
            for message in messages
            if since <= message.date <= until
        )

    async def fetch_message(
        self,
        chat_id: int,
        message_id: int,
    ) -> OneBotMessage | None:
        try:
            payload = await self._client.call(
                "get_msg",
                {"message_id": str(message_id)},
            )
        except OneBotActionError:
            return None
        if not isinstance(payload, dict):
            return None
        try:
            message = OneBotMessage.from_payload(
                payload,
                action_client=self._client,
                private_peer_id=abs(chat_id) if chat_id < 0 else None,
            )
        except OneBotMessageError:
            return None
        return message if message.chat_id == chat_id else None

    async def fetch_after(
        self,
        chat_id: int,
        *,
        after_message_id: int,
        until: datetime,
        limit: int,
    ) -> tuple[OneBotMessage, ...]:
        cursor = await self.fetch_message(chat_id, after_message_id)
        messages = await self._history(chat_id, count=limit)
        cursor_index = next(
            (
                index
                for index, message in enumerate(messages)
                if message.id == after_message_id
            ),
            None,
        )
        if cursor_index is not None:
            candidates = messages[cursor_index + 1 :]
        elif cursor is not None:
            candidates = tuple(
                message for message in messages if message.date > cursor.date
            )
        else:
            # NapCat cannot reverse a short message ID after its cache is lost.
            # Resume from the bounded latest window instead of stalling forever.
            candidates = messages
        return tuple(message for message in candidates if message.date <= until)

    async def _history(
        self,
        chat_id: int,
        *,
        count: int,
        message_seq: int | None = None,
        scope_display_name: str | None = None,
    ) -> tuple[OneBotMessage, ...]:
        if chat_id > 0:
            action = "get_group_msg_history"
            params: dict[str, Any] = {"group_id": str(chat_id)}
        else:
            action = "get_friend_msg_history"
            params = {"user_id": str(abs(chat_id))}
        params.update(
            {
                "count": count,
                "reverse_order": False,
                "disable_get_url": False,
                "parse_mult_msg": True,
            }
        )
        if message_seq is not None:
            params["message_seq"] = str(message_seq)
        response = await self._client.call(action, params, timeout=60)
        payloads = response.get("messages") if isinstance(response, dict) else None
        if not isinstance(payloads, list):
            raise RuntimeError("OneBot history response is malformed")
        parsed: list[tuple[int, OneBotMessage]] = []
        for index, payload in enumerate(payloads):
            try:
                message = OneBotMessage.from_payload(
                    payload,
                    action_client=self._client,
                    scope_display_name=(
                        scope_display_name
                        or (
                            self._directory.scope_name(chat_id)
                            if self._directory is not None
                            else None
                        )
                    ),
                    private_peer_id=abs(chat_id) if chat_id < 0 else None,
                )
            except OneBotMessageError:
                continue
            if message.chat_id == chat_id:
                parsed.append((index, message))
        parsed.sort(key=lambda item: (item[1].date, item[0]))
        return tuple(message for _, message in parsed)


class OneBotMessageIdentityResolver:
    async def resolve(self, message: ReplyTarget) -> MessageIdentity:
        return MessageIdentity(
            subject_id=(
                QQ_IDENTITY_CODEC.actor_id(message.sender_id)
                if message.sender_id is not None
                else None
            ),
            subject_display_name=getattr(message, "sender_display_name", None),
            scope_display_name=getattr(message, "scope_display_name", None),
            is_human=message.sender_id is not None,
        )


class OneBotMessageMentionResolver:
    def __init__(self, directory: OneBotDirectory | None = None):
        self._directory = directory

    async def resolve(self, message: ReplyTarget) -> tuple[MentionedUser, ...]:
        resolved: dict[int, MentionedUser] = {}
        for segment in getattr(message, "segments", ()):
            if segment.get("type") != "at":
                continue
            data = segment.get("data")
            if not isinstance(data, dict):
                continue
            user_id = _positive_int(data.get("qq"))
            if user_id is None:
                continue
            name = data.get("name")
            resolved[user_id] = MentionedUser(
                user_id=user_id,
                display_name=(
                    " ".join(name.split())[:256]
                    if isinstance(name, str) and name.strip()
                    else (
                        self._directory.user_name(user_id)
                        if self._directory is not None
                        else None
                    )
                ),
            )
        return tuple(resolved.values())


class OneBotMemoryScopeTargetResolver:
    def __init__(self, client: OneBotActionClient):
        self._client = client

    async def resolve(
        self,
        target: str,
        *,
        include_latest_message: bool = False,
    ) -> MemoryScopeTarget:
        group_id = _positive_int(target)
        if group_id is None:
            raise ValueError("QQ target must be a positive numeric group ID")
        info = await self._client.call(
            "get_group_info",
            {"group_id": str(group_id), "no_cache": False},
        )
        if not isinstance(info, dict):
            raise ValueError("QQ group is inaccessible")
        display_name = info.get("group_name")
        latest_message_id = 0
        if include_latest_message:
            messages = await OneBotHistorySource(self._client)._history(
                group_id,
                count=1,
                scope_display_name=(
                    display_name if isinstance(display_name, str) else None
                ),
            )
            if messages:
                latest_message_id = messages[-1].id
        return MemoryScopeTarget(
            chat_id=group_id,
            display_name=(
                " ".join(display_name.split())[:256]
                if isinstance(display_name, str) and display_name.strip()
                else None
            ),
            latest_message_id=latest_message_id,
        )


def onebot_memory_event_metadata(message: ReplyTarget) -> dict[str, Any]:
    role = getattr(message, "sender_role", None)
    return {"sender_role": role} if isinstance(role, str) and role else {}


def onebot_source_retry_delay(exc: Exception) -> float | None:
    if isinstance(exc, (ConnectionError, TimeoutError, asyncio.TimeoutError)):
        return 1.0
    return None


def _component(value: ExternalId) -> str:
    if isinstance(value, bool):
        raise ValueError("Boolean values are not valid external IDs")
    normalized = str(value).strip()
    if not normalized:
        raise ValueError("External IDs cannot be empty")
    return quote(normalized, safe="-_.~")


def _positive_component(value: ExternalId) -> str:
    normalized = _component(value)
    parsed = _positive_int(normalized)
    if parsed is None:
        raise ValueError("QQ actor IDs must be positive integers")
    return str(parsed)


def _signed_scope(value: ExternalId) -> int:
    if isinstance(value, bool):
        raise ValueError("QQ scope IDs must be signed integers")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("QQ scope IDs must be signed integers") from exc
    if parsed == 0:
        raise ValueError("QQ scope ID cannot be zero")
    return parsed


def _scope_components(value: ExternalId) -> tuple[str, str]:
    parsed = _signed_scope(value)
    return ("group", str(parsed)) if parsed > 0 else ("private", str(abs(parsed)))


def _positive_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value > 0 else None
    if isinstance(value, str) and value.isascii() and value.isdecimal():
        parsed = int(value)
        return parsed if parsed > 0 else None
    return None


def _message_time(message: ReplyTarget) -> datetime:
    value = getattr(message, "date", None)
    if not isinstance(value, datetime):
        return datetime.now(UTC)
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


async def _gather_directory(
    client: OneBotActionClient,
) -> tuple[Any, Any]:
    return await asyncio.gather(
        client.call("get_friend_list", {"no_cache": False}, timeout=60),
        client.call("get_group_list", {"no_cache": False}, timeout=60),
    )


def _directory_labels(
    values: Any,
    *,
    id_field: str,
    label_fields: tuple[str, ...],
) -> dict[int, str]:
    labels: dict[int, str] = {}
    if not isinstance(values, list):
        return labels
    for value in values:
        if not isinstance(value, dict):
            continue
        identifier = _positive_int(value.get(id_field))
        if identifier is None:
            continue
        label = next(
            (
                " ".join(candidate.split())[:256]
                for field in label_fields
                if isinstance(candidate := value.get(field), str)
                and candidate.strip()
            ),
            None,
        )
        if label:
            labels[identifier] = label
    return labels
