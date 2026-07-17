from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import parse_qs, urlsplit


@dataclass(frozen=True, slots=True)
class TelegramMessageLink:
    username: str | None
    channel_id: int | None
    message_id: int


def parse_telegram_message_link(text: str) -> TelegramMessageLink | None:
    candidate = text.strip()
    if any(character.isspace() for character in candidate):
        return None
    try:
        link = urlsplit(candidate)
    except ValueError:
        return None
    if link.scheme != "https" or link.netloc.casefold() != "t.me":
        return None
    query_keys = parse_qs(link.query, keep_blank_values=True)
    if any(key.casefold() == "comment" for key in query_keys):
        return None

    path = link.path.strip("/")
    segments = path.split("/") if path else []
    if any(not segment for segment in segments):
        return None

    max_message_id = (1 << 31) - 1
    if segments[:1] == ["c"]:
        if len(segments) not in {3, 4}:
            return None
        channel_id = _parse_positive_id(segments[1], maximum=(1 << 63) - 1)
        if (
            len(segments) == 4
            and _parse_positive_id(segments[2], maximum=max_message_id) is None
        ):
            return None
        message_id = _parse_positive_id(segments[-1], maximum=max_message_id)
        if channel_id is None or message_id is None:
            return None
        return TelegramMessageLink(
            username=None,
            channel_id=channel_id,
            message_id=message_id,
        )

    if len(segments) not in {2, 3}:
        return None
    username = segments[0]
    if (
        len(username) > 64
        or not username.isascii()
        or not username.replace("_", "").isalnum()
    ):
        return None
    if (
        len(segments) == 3
        and _parse_positive_id(segments[1], maximum=max_message_id) is None
    ):
        return None
    message_id = _parse_positive_id(segments[-1], maximum=max_message_id)
    if message_id is None:
        return None
    return TelegramMessageLink(
        username=username,
        channel_id=None,
        message_id=message_id,
    )


def _parse_positive_id(value: str, *, maximum: int) -> int | None:
    if not value.isascii() or not value.isdecimal():
        return None
    parsed = int(value)
    return parsed if 0 < parsed <= maximum else None
