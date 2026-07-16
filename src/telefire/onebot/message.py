from __future__ import annotations

import asyncio
import base64
import ipaddress
import mimetypes
import socket
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlsplit

import aiohttp


class OneBotActionClient(Protocol):
    async def call(
        self,
        action: str,
        params: dict[str, Any] | None = None,
        *,
        timeout: float | None = 30,
    ) -> Any: ...


class OneBotMessageError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class OneBotFile:
    kind: str
    token: str
    name: str | None
    mime_type: str
    size: int | None
    url: str | None


@dataclass(slots=True)
class OneBotMessage:
    id: int
    chat_id: int
    raw_text: str
    sender_id: int
    reply_to_msg_id: int | None
    date: datetime
    out: bool
    self_id: int
    message_type: str
    segments: tuple[dict[str, Any], ...]
    sender_display_name: str | None
    scope_display_name: str | None
    sender_role: str | None
    file: OneBotFile | None
    action_client: OneBotActionClient
    post: bool = False
    grouped_id: int | None = None

    @property
    def entities(self) -> tuple[dict[str, Any], ...]:
        return tuple(segment for segment in self.segments if segment["type"] == "at")

    @property
    def is_outgoing(self) -> bool:
        return self.out

    @classmethod
    def from_payload(
        cls,
        payload: dict[str, Any],
        *,
        action_client: OneBotActionClient,
        scope_display_name: str | None = None,
        private_peer_id: int | None = None,
    ) -> OneBotMessage:
        if not isinstance(payload, dict):
            raise OneBotMessageError("OneBot message must be an object")
        message_type = payload.get("message_type")
        if message_type not in {"group", "private"}:
            raise OneBotMessageError("Unsupported OneBot message type")
        message_id = _required_int(payload.get("message_id"), "message_id")
        self_id = _required_int(payload.get("self_id"), "self_id")
        sender_id = _required_int(payload.get("user_id"), "user_id")
        raw_segments = payload.get("message")
        if not isinstance(raw_segments, list):
            raise OneBotMessageError("OneBot array message format is required")
        segments = tuple(_segment(item) for item in raw_segments)

        if message_type == "group":
            chat_id = _required_int(payload.get("group_id"), "group_id")
            if chat_id <= 0:
                raise OneBotMessageError("OneBot group ID must be positive")
            resolved_scope_name = _clean_text(payload.get("group_name"))
        else:
            target_id = _optional_int(payload.get("target_id"))
            peer_id = (
                target_id or private_peer_id
                if sender_id == self_id
                else sender_id
            )
            if peer_id is None or peer_id <= 0:
                raise OneBotMessageError("OneBot private peer ID is unavailable")
            chat_id = -peer_id
            resolved_scope_name = None

        sender = payload.get("sender")
        if not isinstance(sender, dict):
            sender = {}
        sender_display_name = (
            _clean_text(sender.get("card"))
            or _clean_text(sender.get("nickname"))
        )
        reply_to_msg_id = next(
            (
                _optional_int(segment["data"].get("id"))
                for segment in segments
                if segment["type"] == "reply"
            ),
            None,
        )
        text_parts: list[str] = []
        for segment in segments:
            data = segment["data"]
            if segment["type"] == "text":
                text_parts.append(str(data.get("text", "")))
            elif segment["type"] == "at":
                mention = _clean_text(data.get("name")) or _clean_text(data.get("qq"))
                if mention:
                    text_parts.append(f"@{mention}")
            elif segment["type"] == "markdown":
                text_parts.append(str(data.get("content", "")))

        timestamp = payload.get("time")
        if isinstance(timestamp, bool) or not isinstance(timestamp, (int, float)):
            raise OneBotMessageError("OneBot message time is invalid")
        post_type = payload.get("post_type")
        out = post_type == "message_sent" or sender_id == self_id
        return cls(
            id=message_id,
            chat_id=chat_id,
            raw_text="".join(text_parts).strip(),
            sender_id=sender_id,
            reply_to_msg_id=reply_to_msg_id,
            date=datetime.fromtimestamp(timestamp, UTC),
            out=out,
            self_id=self_id,
            message_type=message_type,
            segments=segments,
            sender_display_name=sender_display_name,
            scope_display_name=scope_display_name or resolved_scope_name,
            sender_role=_clean_text(sender.get("role")),
            file=_first_file(segments),
            action_client=action_client,
        )

    async def download_media(self, *, file: type[bytes]) -> bytes | None:
        if file is not bytes:
            raise ValueError("OneBot media download only supports in-memory bytes")
        attachment = self.file
        if attachment is None:
            return None
        url = attachment.url
        if not url:
            response = await self.action_client.call(
                "get_file",
                {"file": attachment.token},
                timeout=30,
            )
            if not isinstance(response, dict):
                return None
            encoded = response.get("base64")
            if isinstance(encoded, str):
                decoded = base64.b64decode(encoded, validate=True)
                if len(decoded) > 5 * 1024 * 1024:
                    return None
                return decoded
            candidate = response.get("url")
            url = candidate if isinstance(candidate, str) else None
        if not url:
            return None
        return await _download_public_url(url, maximum=5 * 1024 * 1024)


def _segment(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise OneBotMessageError("OneBot message segment must be an object")
    segment_type = value.get("type")
    data = value.get("data")
    if not isinstance(segment_type, str) or not isinstance(data, dict):
        raise OneBotMessageError("OneBot message segment is malformed")
    return {"type": segment_type, "data": data}


def _first_file(segments: tuple[dict[str, Any], ...]) -> OneBotFile | None:
    kinds = {
        "image": "image",
        "file": "file",
        "record": "audio",
        "video": "video",
    }
    for segment in segments:
        kind = kinds.get(segment["type"])
        if kind is None:
            continue
        data = segment["data"]
        token = _clean_text(data.get("file")) or _clean_text(data.get("file_id"))
        if not token:
            continue
        name = _safe_filename(data.get("name") or data.get("file_name") or token)
        mime_type = _mime_type(kind, name)
        return OneBotFile(
            kind=kind,
            token=token,
            name=name,
            mime_type=mime_type,
            size=_optional_int(data.get("file_size") or data.get("size")),
            url=_clean_text(data.get("url")),
        )
    return None


def _mime_type(kind: str, filename: str | None) -> str:
    guessed = mimetypes.guess_type(filename or "")[0]
    if guessed:
        return guessed
    return {
        "image": "image/jpeg",
        "audio": "audio/mpeg",
        "video": "video/mp4",
    }.get(kind, "application/octet-stream")


def _safe_filename(value: Any) -> str | None:
    cleaned = _clean_text(value)
    if not cleaned:
        return None
    return Path(cleaned.replace("\\", "/")).name[:200] or None


def _clean_text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = " ".join(value.split()).strip()
    return cleaned[:500] or None


def _required_int(value: Any, field: str) -> int:
    parsed = _optional_int(value)
    if parsed is None:
        raise OneBotMessageError(f"OneBot {field} is invalid")
    return parsed


def _optional_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isascii() and value.isdecimal():
        return int(value)
    return None


async def _download_public_url(url: str, *, maximum: int) -> bytes | None:
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return None
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    addresses = await asyncio.get_running_loop().getaddrinfo(
        parsed.hostname,
        port,
        type=socket.SOCK_STREAM,
    )
    if not addresses:
        return None
    for _, _, _, _, sockaddr in addresses:
        address = ipaddress.ip_address(sockaddr[0])
        if not address.is_global:
            return None
    timeout = aiohttp.ClientTimeout(total=30)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get(url, allow_redirects=False) as response:
            if response.status != 200:
                return None
            content_length = response.content_length
            if content_length is not None and content_length > maximum:
                return None
            content = bytearray()
            async for chunk in response.content.iter_chunked(64 * 1024):
                content.extend(chunk)
                if len(content) > maximum:
                    return None
            return bytes(content) or None
