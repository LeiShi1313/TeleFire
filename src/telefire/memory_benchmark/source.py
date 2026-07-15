from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
from typing import Any
from urllib.parse import quote

import aiohttp


@dataclass(frozen=True, slots=True)
class EpisodeEvent:
    source_id: str
    actor_id: str
    actor_name: str
    text: str
    occurred_at: str
    mentioned_at: str
    reply_to_source_id: str | None


@dataclass(frozen=True, slots=True)
class SourceDocument:
    document_id: str
    content: str
    context: str
    timestamp: str
    content_hash: str
    events: tuple[EpisodeEvent, ...]


@dataclass(frozen=True, slots=True)
class SourceCorpus:
    bank_id: str
    bank_name: str
    exported_at: str
    documents: tuple[SourceDocument, ...]

    @property
    def events(self) -> tuple[EpisodeEvent, ...]:
        return tuple(event for document in self.documents for event in document.events)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "telefire.memory-benchmark.source.v1",
            "bank_id": self.bank_id,
            "bank_name": self.bank_name,
            "exported_at": self.exported_at,
            "stats": {
                "documents": len(self.documents),
                "events": len(self.events),
                "characters": sum(len(document.content) for document in self.documents),
            },
            "documents": [asdict(document) for document in self.documents],
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> SourceCorpus:
        if value.get("schema") != "telefire.memory-benchmark.source.v1":
            raise ValueError("Unsupported benchmark source schema")
        documents = []
        for supplied in value.get("documents", []):
            document = dict(supplied)
            supplied_events = document.pop("events", None)
            if not isinstance(supplied_events, list):
                raise ValueError("Benchmark source document has invalid events")
            events = tuple(EpisodeEvent(**event) for event in supplied_events)
            documents.append(SourceDocument(events=events, **document))
        return cls(
            bank_id=value["bank_id"],
            bank_name=value["bank_name"],
            exported_at=value["exported_at"],
            documents=tuple(documents),
        )


def parse_source_document(payload: dict[str, Any]) -> SourceDocument:
    document_id = _required_string(payload, "id")
    content = _required_string(payload, "original_text")
    try:
        episode = json.loads(content)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Document {document_id} is not valid Episode JSON") from exc
    if not isinstance(episode, dict) or episode.get("schema") != "telefire.memory.episode.v1":
        raise ValueError(f"Document {document_id} has an unsupported Episode schema")
    supplied_events = episode.get("events")
    if not isinstance(supplied_events, list) or not supplied_events:
        raise ValueError(f"Document {document_id} has no events")

    events = []
    for supplied in supplied_events:
        if not isinstance(supplied, dict):
            raise ValueError(f"Document {document_id} has a malformed event")
        actor = supplied.get("actor")
        if not isinstance(actor, dict):
            raise ValueError(f"Document {document_id} has an event without an actor")
        occurred_at = _iso_timestamp(supplied.get("occurred_at"))
        mentioned_at = supplied.get("mentioned_at")
        events.append(
            EpisodeEvent(
                source_id=_required_string(supplied, "source_id"),
                actor_id=_required_string(actor, "id"),
                actor_name=_required_string(actor, "display_name"),
                text=_required_string(supplied, "text"),
                occurred_at=occurred_at,
                mentioned_at=(
                    _iso_timestamp(mentioned_at)
                    if mentioned_at is not None
                    else occurred_at
                ),
                reply_to_source_id=_optional_string(supplied, "reply_to_source_id"),
            )
        )

    retain = payload.get("retain_params") or {}
    metadata = payload.get("document_metadata") or {}
    timestamp = retain.get("event_date") or events[-1].occurred_at
    context = retain.get("context") or "telegram conversation"
    content_hash = metadata.get("content_hash") or hashlib.sha256(content.encode()).hexdigest()
    if not all(isinstance(item, str) for item in (timestamp, context, content_hash)):
        raise ValueError(f"Document {document_id} has malformed retain metadata")
    return SourceDocument(
        document_id=document_id,
        content=content,
        context=context,
        timestamp=_iso_timestamp(timestamp),
        content_hash=content_hash,
        events=tuple(events),
    )


async def export_hindsight_bank(
    base_url: str,
    bank_id: str,
    bank_name: str,
    *,
    concurrency: int = 8,
) -> SourceCorpus:
    timeout = aiohttp.ClientTimeout(total=120)
    encoded_bank = quote(bank_id, safe="")
    async with aiohttp.ClientSession(timeout=timeout) as session:
        summaries: list[dict[str, Any]] = []
        offset = 0
        while True:
            page = await _json_request(
                session,
                "GET",
                f"{base_url.rstrip('/')}/v1/default/banks/{encoded_bank}/documents",
                params={"limit": "100", "offset": str(offset)},
            )
            items = page.get("items")
            total = page.get("total")
            if not isinstance(items, list) or not isinstance(total, int):
                raise ValueError("Hindsight returned a malformed document page")
            summaries.extend(items)
            offset += len(items)
            if offset >= total:
                break
            if not items:
                raise ValueError("Hindsight document pagination stopped early")

        semaphore = asyncio.Semaphore(concurrency)

        async def detail(summary: dict[str, Any]) -> SourceDocument:
            document_id = _required_string(summary, "id")
            async with semaphore:
                payload = await _json_request(
                    session,
                    "GET",
                    f"{base_url.rstrip('/')}/v1/default/banks/{encoded_bank}/documents/"
                    f"{quote(document_id, safe='')}",
                )
            return parse_source_document(payload)

        documents = await asyncio.gather(*(detail(summary) for summary in summaries))

    documents.sort(key=lambda document: (document.timestamp, document.document_id))
    return SourceCorpus(
        bank_id=bank_id,
        bank_name=bank_name,
        exported_at=datetime.now(UTC).isoformat(),
        documents=tuple(documents),
    )


def write_corpus(corpus: SourceCorpus, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(corpus.to_dict(), ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )


def read_corpus(path: Path) -> SourceCorpus:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("Benchmark source must be a JSON object")
    return SourceCorpus.from_dict(value)


def tencent_seed_payload(corpus: SourceCorpus) -> dict[str, Any]:
    conversations = []
    for document in corpus.documents:
        lines = [f"[Source document: {document.document_id}]"]
        lines.extend(
            f"[{event.occurred_at}] [Telegram actor: {event.actor_name} | "
            f"{event.actor_id}] {event.text}"
            for event in document.events
        )
        conversations.append(
            [
                {
                    "role": "user",
                    "content": "\n".join(lines),
                    "timestamp": document.timestamp,
                }
            ]
        )
    return {
        "sessions": [
            {
                "sessionKey": corpus.bank_id,
                "sessionId": f"benchmark:{corpus.bank_id}",
                "conversations": conversations,
            }
        ]
    }


async def _json_request(
    session: aiohttp.ClientSession,
    method: str,
    url: str,
    **kwargs: Any,
) -> dict[str, Any]:
    async with session.request(method, url, **kwargs) as response:
        text = await response.text()
        if response.status >= 400:
            raise RuntimeError(f"{method} {url} failed with status {response.status}: {text[:500]}")
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{method} {url} returned invalid JSON") from exc
        if not isinstance(payload, dict):
            raise ValueError(f"{method} {url} returned a non-object response")
        return payload


def _required_string(payload: dict[str, Any], name: str) -> str:
    value = payload.get(name)
    if not isinstance(value, str) or not value:
        raise ValueError(f"Missing or invalid {name}")
    return value


def _optional_string(payload: dict[str, Any], name: str) -> str | None:
    value = payload.get(name)
    if value is not None and not isinstance(value, str):
        raise ValueError(f"Invalid {name}")
    return value


def _iso_timestamp(value: Any) -> str:
    if not isinstance(value, str):
        raise ValueError("Missing or invalid timestamp")
    normalized = value.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ValueError(f"Invalid timestamp: {value}") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC).isoformat().replace("+00:00", "Z")
