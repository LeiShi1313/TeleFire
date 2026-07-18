from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
from typing import Any, Iterable

from telethon import TelegramClient, utils as telegram_utils

from telefire.memory_benchmark.source import (
    EpisodeEvent,
    SourceCorpus,
    SourceDocument,
)
from telefire.telegram.config import TelegramRuntimeConfig


TIMELINE_LEDGER_SCHEMA = "telefire.memory-benchmark.telegram-timeline.v1"


@dataclass(frozen=True, slots=True)
class TimelineExport:
    corpus: SourceCorpus
    rows: tuple[dict[str, Any], ...]
    stats: dict[str, int]


async def download_telegram_timeline(
    channel: str,
    *,
    limit: int,
    account: str = "default",
    session_path: str | Path | None = None,
) -> TimelineExport:
    if limit < 1:
        raise ValueError("Telegram export limit must be positive")
    config = TelegramRuntimeConfig.from_account(account=account)
    selected_session = (
        Path(session_path).expanduser().resolve()
        if session_path is not None
        else (config.store_dir / config.session_name).expanduser()
    )
    session_file = _session_file(selected_session)
    if session_file.exists() and session_file.stat().st_mode & 0o077:
        raise PermissionError(f"Telegram session must be private (0600): {session_file}")
    client = TelegramClient(
        str(selected_session),
        config.api_id,
        config.api_hash,
    )
    try:
        await client.connect()
        if not await client.is_user_authorized():
            raise RuntimeError(f"Telegram account {account!r} is not authorized")
        entity = await client.get_entity(channel)
        channel_id = telegram_utils.get_peer_id(entity)
        channel_title = str(
            getattr(entity, "title", None)
            or getattr(entity, "username", None)
            or channel
        )
        messages = [
            message
            async for message in client.iter_messages(entity, limit=limit)
        ]
    finally:
        await client.disconnect()
        if session_file.exists():
            session_file.chmod(0o600)

    rows = tuple(
        sorted(
            (_timeline_row(message, channel_id) for message in messages),
            key=lambda row: (row["occurred_at"], row["message_id"]),
        )
    )
    return timeline_to_corpus(
        channel_id=channel_id,
        channel_title=channel_title,
        exported_at=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        rows=rows,
    )


def timeline_to_corpus(
    *,
    channel_id: int,
    channel_title: str,
    exported_at: str,
    rows: Iterable[dict[str, Any]],
) -> TimelineExport:
    normalized_rows = _validated_timeline_rows(
        rows,
        expected_chat_id=channel_id,
    )
    logical_groups: dict[tuple[str, int], list[dict[str, Any]]] = {}
    for row in normalized_rows:
        message_id = int(row["message_id"])
        grouped_id = row.get("grouped_id")
        key = (
            ("album", int(grouped_id))
            if isinstance(grouped_id, int)
            else ("message", message_id)
        )
        logical_groups.setdefault(key, []).append(row)

    documents: list[SourceDocument] = []
    ordered_groups = sorted(
        logical_groups.items(),
        key=lambda item: (
            str(item[1][0]["occurred_at"]),
            int(item[1][0]["message_id"]),
        ),
    )
    for (group_kind, group_id), group in ordered_groups:
        events = tuple(
            event
            for row in group
            if (event := _row_event(row, channel_id, channel_title)) is not None
        )
        if not events:
            continue
        text = "\n\n".join(event.text for event in events)
        document_id = (
            f"telegram:channel-album:{channel_id}:{group_id}"
            if group_kind == "album"
            else f"telegram:timeline-item:{channel_id}:{group_id}"
        )
        documents.append(
            SourceDocument(
                document_id=document_id,
                content=text,
                context=f"telegram timeline item in {channel_title}",
                timestamp=events[-1].occurred_at,
                content_hash=hashlib.sha256(text.encode("utf-8")).hexdigest(),
                events=events,
            )
        )

    with_media = sum(bool(row.get("has_media")) for row in normalized_rows)
    textual = sum(len(document.events) for document in documents)
    return TimelineExport(
        corpus=SourceCorpus(
            bank_id=f"telegram:chat:{channel_id}",
            bank_name=channel_title,
            exported_at=_iso_timestamp(exported_at),
            documents=tuple(documents),
        ),
        rows=normalized_rows,
        stats={
            "downloaded": len(normalized_rows),
            "textual": textual,
            "with_media": with_media,
            "blank_media": sum(
                bool(row.get("has_media"))
                and not str(row.get("text") or "").strip()
                for row in normalized_rows
            ),
        },
    )


def enrich_timeline_rows(
    rows: Iterable[dict[str, Any]],
    retained_corpus: SourceCorpus,
) -> tuple[dict[str, Any], ...]:
    chat_id = _telegram_chat_id(retained_corpus.bank_id)
    normalized_rows = _validated_timeline_rows(rows, expected_chat_id=chat_id)
    retained_by_source_id = {
        event.source_id: event
        for event in retained_corpus.events
        if event.source_id.startswith(f"telegram:message:{chat_id}:")
    }
    enriched = []
    for supplied in normalized_rows:
        row = dict(supplied)
        retained = retained_by_source_id.get(str(row["source_id"]))
        if retained is None:
            row["memory_text"] = str(row.get("text") or "").strip()
        else:
            row["memory_text"] = retained.text
            row["memory_actor_id"] = retained.actor_id
            row["memory_actor_name"] = retained.actor_name
            row["memory_mentioned_at"] = retained.mentioned_at
        enriched.append(row)
    return tuple(enriched)


def write_timeline_rows(rows: Iterable[dict[str, Any]], path: Path) -> None:
    normalized_rows = _validated_timeline_rows(rows)
    chat_id = int(normalized_rows[0]["chat_id"])
    manifest = {
        "schema": TIMELINE_LEDGER_SCHEMA,
        "kind": "manifest",
        "platform": "telegram",
        "chat_id": chat_id,
        "rows": len(normalized_rows),
        "rows_sha256": _timeline_rows_digest(normalized_rows),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as output:
        output.write(json.dumps(manifest, ensure_ascii=False, separators=(",", ":")))
        output.write("\n")
        for row in normalized_rows:
            output.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")))
            output.write("\n")
    path.chmod(0o600)


def read_timeline_rows(path: Path) -> tuple[dict[str, Any], ...]:
    values = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ValueError("Timeline JSONL rows must be objects")
        values.append(value)
    if not values:
        raise ValueError("Timeline JSONL is empty")
    manifest, *rows = values
    if (
        manifest.get("schema") != TIMELINE_LEDGER_SCHEMA
        or manifest.get("kind") != "manifest"
        or manifest.get("platform") != "telegram"
        or not isinstance(manifest.get("chat_id"), int)
    ):
        raise ValueError("Timeline JSONL has an invalid manifest")
    normalized_rows = _validated_timeline_rows(
        rows,
        expected_chat_id=int(manifest["chat_id"]),
    )
    if (
        manifest.get("rows") != len(normalized_rows)
        or manifest.get("rows_sha256") != _timeline_rows_digest(normalized_rows)
    ):
        raise ValueError("Timeline JSONL manifest does not match its rows")
    return normalized_rows


def _timeline_row(message: Any, channel_id: int) -> dict[str, Any]:
    occurred_at = message.date
    if occurred_at.tzinfo is None:
        occurred_at = occurred_at.replace(tzinfo=UTC)
    file = getattr(message, "file", None)
    media_type = getattr(file, "mime_type", None) if file is not None else None
    if not isinstance(media_type, str):
        media_type = type(message.media).__name__ if message.media is not None else None
    return {
        "platform": "telegram",
        "chat_id": channel_id,
        "message_id": int(message.id),
        "source_id": f"telegram:message:{channel_id}:{int(message.id)}",
        "occurred_at": occurred_at.astimezone(UTC)
        .isoformat()
        .replace("+00:00", "Z"),
        "text": str(message.raw_text or "").strip(),
        "sender_id": (
            int(message.sender_id) if isinstance(message.sender_id, int) else None
        ),
        "reply_to_message_id": (
            int(message.reply_to_msg_id)
            if isinstance(message.reply_to_msg_id, int)
            else None
        ),
        "grouped_id": (
            int(message.grouped_id) if isinstance(message.grouped_id, int) else None
        ),
        "post_author": (
            str(message.post_author).strip()
            if isinstance(message.post_author, str) and message.post_author.strip()
            else None
        ),
        "has_media": message.media is not None,
        "media_type": media_type,
    }


def _row_event(
    row: dict[str, Any],
    channel_id: int,
    channel_title: str,
) -> EpisodeEvent | None:
    text = str(row.get("memory_text", row.get("text")) or "").strip()
    if not text:
        return None
    occurred_at = _iso_timestamp(str(row["occurred_at"]))
    sender_id = row.get("sender_id")
    actor_component = int(sender_id) if isinstance(sender_id, int) else abs(channel_id)
    reply_to = row.get("reply_to_message_id")
    return EpisodeEvent(
        source_id=str(row["source_id"]),
        actor_id=str(row.get("memory_actor_id") or f"telegram:channel:{actor_component}"),
        actor_name=str(row.get("memory_actor_name") or channel_title),
        text=text,
        occurred_at=occurred_at,
        mentioned_at=_iso_timestamp(str(row.get("memory_mentioned_at") or occurred_at)),
        reply_to_source_id=(
            f"telegram:message:{channel_id}:{int(reply_to)}"
            if isinstance(reply_to, int)
            else None
        ),
    )


def _validated_timeline_rows(
    rows: Iterable[dict[str, Any]],
    *,
    expected_chat_id: int | None = None,
) -> tuple[dict[str, Any], ...]:
    normalized = tuple(
        sorted(
            (dict(row) for row in rows),
            key=lambda row: (str(row["occurred_at"]), int(row["message_id"])),
        )
    )
    if not normalized:
        raise ValueError("Timeline ledger has no messages")
    supplied_chat_ids = [row.get("chat_id") for row in normalized]
    if not all(isinstance(value, int) for value in supplied_chat_ids):
        raise ValueError("Timeline ledger must contain one Telegram chat identity")
    chat_ids = set(supplied_chat_ids)
    if len(chat_ids) != 1:
        raise ValueError("Timeline ledger must contain one Telegram chat identity")
    chat_id = int(next(iter(chat_ids)))
    if expected_chat_id is not None and chat_id != expected_chat_id:
        raise ValueError("Timeline ledger and retained corpus identify different chats")
    for row in normalized:
        message_id = row.get("message_id")
        if (
            row.get("platform") != "telegram"
            or not isinstance(message_id, int)
            or row.get("source_id")
            != f"telegram:message:{chat_id}:{message_id}"
        ):
            raise ValueError("Timeline row has an invalid Telegram source identity")
    return normalized


def _timeline_rows_digest(rows: tuple[dict[str, Any], ...]) -> str:
    payload = json.dumps(
        rows,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _telegram_chat_id(bank_id: str) -> int:
    prefix = "telegram:chat:"
    if not bank_id.startswith(prefix):
        raise ValueError("Retained source does not identify a Telegram chat")
    try:
        return int(bank_id.removeprefix(prefix))
    except ValueError as exc:
        raise ValueError("Retained source has an invalid Telegram chat ID") from exc


def _session_file(session_path: Path) -> Path:
    return (
        session_path
        if session_path.suffix == ".session"
        else Path(f"{session_path}.session")
    )


def _iso_timestamp(value: str) -> str:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC).isoformat().replace("+00:00", "Z")
