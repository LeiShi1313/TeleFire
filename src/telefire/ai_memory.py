from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal, Protocol
from urllib.parse import quote

import aiohttp


MemoryUpdateMode = Literal["replace", "append"]


@dataclass(frozen=True, slots=True)
class MemoryEvent:
    source_id: str | None
    actor_id: str
    actor_display_name: str | None
    occurred_at: datetime
    text: str
    mentioned_at: datetime | None = None
    reply_to_source_id: str | None = None
    mentioned_actors: tuple[tuple[str, str | None], ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        occurred_at = self.occurred_at
        if occurred_at.tzinfo is None:
            occurred_at = occurred_at.replace(tzinfo=UTC)
        mentioned_at = self.mentioned_at
        if mentioned_at is not None and mentioned_at.tzinfo is None:
            mentioned_at = mentioned_at.replace(tzinfo=UTC)
        return {
            "source_id": self.source_id,
            "actor": {
                "id": self.actor_id,
                "display_name": self.actor_display_name,
            },
            "occurred_at": occurred_at.astimezone(UTC)
            .isoformat()
            .replace("+00:00", "Z"),
            "mentioned_at": (
                mentioned_at.astimezone(UTC).isoformat().replace("+00:00", "Z")
                if mentioned_at is not None
                else None
            ),
            "reply_to_source_id": self.reply_to_source_id,
            "mentioned_actors": [
                {"id": actor_id, "display_name": display_name}
                for actor_id, display_name in self.mentioned_actors
            ],
            "metadata": self.metadata,
            "text": self.text,
        }


@dataclass(frozen=True, slots=True)
class MemoryEpisode:
    scope_id: str
    document_id: str
    events: tuple[MemoryEvent, ...]
    scope_display_name: str | None = None
    source: str = "chat"

    def __post_init__(self) -> None:
        if not self.scope_id or not self.document_id:
            raise ValueError("Memory Episode requires scope and document identities")
        if not self.events:
            raise ValueError("Memory Episode requires at least one event")
        source_ids = [
            event.source_id for event in self.events if event.source_id is not None
        ]
        if len(source_ids) != len(set(source_ids)):
            raise ValueError("Memory Episode source IDs must be unique")

    @property
    def content(self) -> str:
        payload = {
            "schema": "telefire.memory.episode.v1",
            "scope": {
                "id": self.scope_id,
                "display_name": self.scope_display_name,
            },
            "events": [event.to_dict() for event in self.events],
        }
        return json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )

    @property
    def content_hash(self) -> str:
        return hashlib.sha256(self.content.encode("utf-8")).hexdigest()

    @property
    def actor_ids(self) -> tuple[str, ...]:
        actors: dict[str, None] = {}
        for event in self.events:
            actors[event.actor_id] = None
            for actor_id, _ in event.mentioned_actors:
                actors[actor_id] = None
        return tuple(actors)

    @property
    def event_versions(self) -> tuple[tuple[str, str], ...]:
        versions: list[tuple[str, str]] = []
        for index, event in enumerate(self.events):
            source_id = event.source_id or f"position:{index}"
            content = json.dumps(
                event.to_dict(),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            versions.append(
                (source_id, hashlib.sha256(content.encode("utf-8")).hexdigest())
            )
        return tuple(versions)


@dataclass(frozen=True, slots=True)
class MemoryRetainResult:
    accepted: bool
    operation_id: str | None = None
    items_count: int = 1


@dataclass(frozen=True, slots=True)
class RecalledMemory:
    memory_id: str
    text: str
    memory_type: str | None
    entities: tuple[str, ...]
    occurred_start: str | None
    occurred_end: str | None
    mentioned_at: str | None
    document_id: str | None
    chunk_id: str | None


@dataclass(frozen=True, slots=True)
class MemoryRecall:
    scope_id: str
    memories: tuple[RecalledMemory, ...]

    def render(self, *, max_chars: int = 4_000) -> str:
        if max_chars < 1 or not self.memories:
            return ""
        sections = ["Relevant evidence recalled from this chat bank:"]
        for memory in self.memories:
            details: list[str] = []
            if memory.memory_type:
                details.append(memory.memory_type)
            if memory.occurred_start:
                occurred = memory.occurred_start
                if memory.occurred_end and memory.occurred_end != occurred:
                    occurred = f"{occurred} to {memory.occurred_end}"
                details.append(f"occurred {occurred}")
            if memory.mentioned_at:
                details.append(f"mentioned {memory.mentioned_at}")
            if memory.entities:
                details.append(f"entities: {', '.join(memory.entities)}")
            if memory.document_id:
                source = memory.document_id
                if memory.chunk_id:
                    source = f"{source}#{memory.chunk_id}"
                details.append(f"source: {source}")
            details.append(f"memory_id: {memory.memory_id}")
            suffix = f" ({'; '.join(details)})" if details else ""
            candidate = f"- {memory.text}{suffix}"
            rendered = "\n".join([*sections, candidate])
            if len(rendered) > max_chars:
                break
            sections.append(candidate)
        rendered = "\n".join(sections)
        return rendered[:max_chars]


@dataclass(frozen=True, slots=True)
class MemoryRevisionResult:
    invalidated_count: int


@dataclass(frozen=True, slots=True)
class MemoryDocumentReceipt:
    content_hash: str
    event_versions: tuple[tuple[str, str], ...] = ()


class MemoryClient(Protocol):
    async def retain(
        self,
        episode: MemoryEpisode,
        *,
        update_mode: MemoryUpdateMode = "replace",
    ) -> MemoryRetainResult: ...

    async def retain_many(
        self,
        episodes: tuple[MemoryEpisode, ...],
        *,
        update_mode: MemoryUpdateMode = "replace",
    ) -> MemoryRetainResult: ...

    async def recall(
        self,
        *,
        scope_id: str,
        query: str,
    ) -> MemoryRecall: ...

    async def revise(
        self,
        *,
        scope_id: str,
        subject_id: str,
        instruction: str,
    ) -> MemoryRevisionResult: ...


class MemoryReceiptStore(Protocol):
    async def get_memory_document_receipt(
        self,
        scope_id: str,
        document_id: str,
    ) -> MemoryDocumentReceipt | None: ...

    async def save_memory_document_receipt(
        self,
        scope_id: str,
        document_id: str,
        content_hash: str,
        event_versions: tuple[tuple[str, str], ...],
    ) -> None: ...


class MemoryClientError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        status: int | None = None,
        retry_after: float | None = None,
    ):
        super().__init__(message)
        self.status = status
        self.retry_after = retry_after


class HindsightMemoryClient:
    def __init__(self, base_url: str, *, timeout: float = 10.0):
        if timeout <= 0:
            raise ValueError("Memory timeout must be positive")
        self._base_url = base_url.rstrip("/")
        if not self._base_url.startswith(("http://", "https://")):
            raise ValueError("Hindsight URL must use http or https")
        self._timeout = aiohttp.ClientTimeout(total=timeout)
        self._session: aiohttp.ClientSession | None = None

    async def close(self) -> None:
        if self._session is not None:
            await self._session.close()
            self._session = None

    async def retain(
        self,
        episode: MemoryEpisode,
        *,
        update_mode: MemoryUpdateMode = "replace",
    ) -> MemoryRetainResult:
        return await self.retain_many((episode,), update_mode=update_mode)

    async def retain_many(
        self,
        episodes: tuple[MemoryEpisode, ...],
        *,
        update_mode: MemoryUpdateMode = "replace",
    ) -> MemoryRetainResult:
        if not episodes:
            return MemoryRetainResult(accepted=True, items_count=0)
        scope_id = episodes[0].scope_id
        if any(episode.scope_id != scope_id for episode in episodes):
            raise ValueError("One Hindsight retain batch cannot span banks")
        payload = await self._request(
            "POST",
            self._bank_path(scope_id, "/memories"),
            {
                "items": [
                    self._retain_item(episode, update_mode=update_mode)
                    for episode in episodes
                ],
                "async": False,
            },
        )
        if (
            payload.get("success") is not True
            or payload.get("bank_id") != scope_id
            or payload.get("items_count") != len(episodes)
        ):
            raise MemoryClientError("Hindsight retain response is malformed")
        operation_id = payload.get("operation_id")
        if operation_id is not None and not isinstance(operation_id, str):
            raise MemoryClientError("Hindsight retain response is malformed")
        return MemoryRetainResult(
            accepted=True,
            operation_id=operation_id,
            items_count=len(episodes),
        )

    async def get_document_content(
        self,
        *,
        scope_id: str,
        document_id: str,
    ) -> str | None:
        try:
            payload = await self._request(
                "GET",
                self._bank_path(
                    scope_id,
                    f"/documents/{quote(document_id, safe='')}",
                ),
            )
        except MemoryClientError as exc:
            if exc.status == 404:
                return None
            raise
        if payload.get("id") != document_id or not isinstance(
            payload.get("original_text"), str
        ):
            raise MemoryClientError("Hindsight document response is malformed")
        return payload["original_text"]

    async def recall(
        self,
        *,
        scope_id: str,
        query: str,
    ) -> MemoryRecall:
        query = query.strip()
        if not query:
            return MemoryRecall(scope_id=scope_id, memories=())
        payload = await self._request(
            "POST",
            self._bank_path(scope_id, "/memories/recall"),
            {
                "query": query[:8_000],
                "budget": "mid",
                "max_tokens": 2_000,
                "types": ["world", "experience", "observation"],
                "include": {
                    "entities": {"max_tokens": 500},
                    "source_facts": {"max_tokens": 750},
                },
            },
        )
        results = payload.get("results")
        if not isinstance(results, list) or len(results) > 1_000:
            raise MemoryClientError("Hindsight recall response is malformed")
        memories: list[RecalledMemory] = []
        for item in results:
            if not isinstance(item, dict):
                raise MemoryClientError("Hindsight recall response is malformed")
            memory_id = item.get("id")
            text = item.get("text")
            if not isinstance(memory_id, str) or not isinstance(text, str):
                raise MemoryClientError("Hindsight recall response is malformed")
            entities = item.get("entities") or []
            if not isinstance(entities, list) or not all(
                isinstance(entity, str) for entity in entities
            ):
                raise MemoryClientError("Hindsight recall response is malformed")
            memories.append(
                RecalledMemory(
                    memory_id=memory_id,
                    text=text,
                    memory_type=_optional_string(item, "type"),
                    entities=tuple(entities),
                    occurred_start=_optional_string(item, "occurred_start"),
                    occurred_end=_optional_string(item, "occurred_end"),
                    mentioned_at=_optional_string(item, "mentioned_at"),
                    document_id=_optional_string(item, "document_id"),
                    chunk_id=_optional_string(item, "chunk_id"),
                )
            )
        return MemoryRecall(scope_id=scope_id, memories=tuple(memories))

    async def revise(
        self,
        *,
        scope_id: str,
        subject_id: str,
        instruction: str,
    ) -> MemoryRevisionResult:
        selection = await self._request(
            "POST",
            self._bank_path(scope_id, "/reflect"),
            {
                "query": self._revision_query(
                    subject_id=subject_id,
                    instruction=instruction,
                ),
                "budget": "mid",
                "max_tokens": 1_500,
                "fact_types": ["world", "experience"],
                "include": {"facts": {"max_tokens": 2_000}},
                "response_schema": {
                    "type": "object",
                    "properties": {
                        "invalidate_memory_ids": {
                            "type": "array",
                            "items": {"type": "string"},
                        }
                    },
                    "required": ["invalidate_memory_ids"],
                    "additionalProperties": False,
                },
            },
        )
        structured = selection.get("structured_output")
        based_on = selection.get("based_on")
        if not isinstance(structured, dict) or not isinstance(based_on, dict):
            raise MemoryClientError("Hindsight revision response is malformed")
        candidate_ids = structured.get("invalidate_memory_ids")
        cited = based_on.get("memories")
        if not isinstance(candidate_ids, list) or not all(
            isinstance(memory_id, str) for memory_id in candidate_ids
        ):
            raise MemoryClientError("Hindsight revision response is malformed")
        if not isinstance(cited, list):
            raise MemoryClientError("Hindsight revision response is malformed")
        cited_ids = {
            item.get("id")
            for item in cited
            if isinstance(item, dict) and isinstance(item.get("id"), str)
        }
        if not set(candidate_ids) <= cited_ids:
            raise MemoryClientError("Hindsight revision selected uncited memory")

        invalidated_count = 0
        for memory_id in dict.fromkeys(candidate_ids):
            memory = await self._request(
                "GET",
                self._bank_path(
                    scope_id,
                    f"/memories/{quote(memory_id, safe='')}",
                ),
            )
            entities = memory.get("entities") or []
            if subject_id not in entities:
                continue
            if memory.get("type") not in {"world", "experience"}:
                continue
            updated = await self._request(
                "PATCH",
                self._bank_path(
                    scope_id,
                    f"/memories/{quote(memory_id, safe='')}",
                ),
                {
                    "state": "invalidated",
                    "reason": f"Owner revision: {instruction[:500]}",
                },
            )
            if updated.get("state") != "invalidated":
                raise MemoryClientError("Hindsight curation response is malformed")
            invalidated_count += 1
        return MemoryRevisionResult(invalidated_count=invalidated_count)

    async def _request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=self._timeout)
        async with self._session.request(
            method,
            f"{self._base_url}{path}",
            json=payload,
        ) as response:
            if response.status < 200 or response.status >= 300:
                retry_after = response.headers.get("Retry-After")
                try:
                    retry_after_seconds = (
                        float(retry_after) if retry_after is not None else None
                    )
                except ValueError:
                    retry_after_seconds = None
                raise MemoryClientError(
                    f"Hindsight request failed with status {response.status}",
                    status=response.status,
                    retry_after=retry_after_seconds,
                )
            try:
                result = await response.json()
            except (aiohttp.ContentTypeError, ValueError) as exc:
                raise MemoryClientError("Hindsight returned invalid JSON") from exc
        if not isinstance(result, dict):
            raise MemoryClientError("Hindsight response must be a JSON object")
        return result

    @staticmethod
    def _episode_context(episode: MemoryEpisode) -> str:
        label = episode.scope_display_name or episode.scope_id
        return f"{episode.source} conversation in {label}"

    @classmethod
    def _retain_item(
        cls,
        episode: MemoryEpisode,
        *,
        update_mode: MemoryUpdateMode,
    ) -> dict[str, Any]:
        latest = episode.events[-1].occurred_at
        if latest.tzinfo is None:
            latest = latest.replace(tzinfo=UTC)
        return {
            "content": episode.content,
            "context": cls._episode_context(episode),
            "timestamp": latest.astimezone(UTC).isoformat().replace("+00:00", "Z"),
            "document_id": episode.document_id,
            "update_mode": update_mode,
            "metadata": {
                "client": "telefire",
                "source": episode.source,
                "scope_id": episode.scope_id,
                "content_hash": episode.content_hash,
            },
            "entities": [
                {"text": actor_id, "type": "PERSON"} for actor_id in episode.actor_ids
            ],
        }

    @staticmethod
    def _revision_query(
        *,
        subject_id: str,
        instruction: str,
    ) -> str:
        return (
            "A trusted owner requested reversible memory curation. Select only cited "
            "world or experience memories that are about the target actor and must be "
            "invalidated to satisfy the instruction. The owner correction is retained "
            "separately as auditable evidence. Return an empty list when no existing "
            f"cited memory clearly matches.\nTarget actor: {subject_id}\n"
            f"Instruction: {instruction}"
        )

    @staticmethod
    def _bank_path(scope_id: str, suffix: str) -> str:
        return f"/v1/default/banks/{quote(scope_id, safe='')}{suffix}"


async def retain_episode_once(
    memory: MemoryClient,
    receipts: MemoryReceiptStore,
    episode: MemoryEpisode,
    *,
    update_mode: MemoryUpdateMode | None = None,
) -> bool:
    return (
        await retain_episodes_once(
            memory,
            receipts,
            (episode,),
            update_mode=update_mode,
        )
    )[0]


async def append_episode_once(
    memory: MemoryClient,
    receipts: MemoryReceiptStore,
    episode: MemoryEpisode,
) -> bool:
    previous = await receipts.get_memory_document_receipt(
        episode.scope_id,
        episode.document_id,
    )
    previous_versions = dict(previous.event_versions) if previous else {}
    if previous is not None and all(
        previous_versions.get(source_id) == version
        for source_id, version in episode.event_versions
    ):
        return False
    result = await memory.retain_many((episode,), update_mode="append")
    if not result.accepted or result.items_count != 1:
        raise MemoryClientError("Hindsight did not accept the appended Episode")
    merged_versions = dict(previous_versions)
    merged_versions.update(episode.event_versions)
    ordered_source_ids = [
        source_id for source_id, _ in (previous.event_versions if previous else ())
    ]
    ordered_source_ids.extend(
        source_id
        for source_id, _ in episode.event_versions
        if source_id not in previous_versions
    )
    event_versions = tuple(
        (source_id, merged_versions[source_id]) for source_id in ordered_source_ids
    )
    # The next complete Dream replacement deliberately differs from this
    # delivery-only hash and canonicalizes Hindsight's appended document body.
    receipt_hash = hashlib.sha256(
        json.dumps(event_versions, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    await receipts.save_memory_document_receipt(
        episode.scope_id,
        episode.document_id,
        receipt_hash,
        event_versions,
    )
    return True


async def _save_receipts(
    receipts: MemoryReceiptStore,
    episodes: tuple[MemoryEpisode, ...],
) -> None:
    await asyncio.gather(
        *(
            receipts.save_memory_document_receipt(
                episode.scope_id,
                episode.document_id,
                episode.content_hash,
                episode.event_versions,
            )
            for episode in episodes
        )
    )


async def retain_episodes_once(
    memory: MemoryClient,
    receipts: MemoryReceiptStore,
    episodes: tuple[MemoryEpisode, ...],
    *,
    update_mode: MemoryUpdateMode | None = None,
) -> tuple[bool, ...]:
    if not episodes:
        return ()
    scope_id = episodes[0].scope_id
    if any(episode.scope_id != scope_id for episode in episodes):
        raise ValueError("One receipt batch cannot span memory scopes")
    previous_receipts = await asyncio.gather(
        *(
            receipts.get_memory_document_receipt(
                episode.scope_id,
                episode.document_id,
            )
            for episode in episodes
        )
    )
    pending = tuple(
        (index, episode, previous)
        for index, (episode, previous) in enumerate(
            zip(episodes, previous_receipts, strict=True)
        )
        if previous is None or previous.content_hash != episode.content_hash
    )
    if not pending:
        return tuple(False for _ in episodes)
    created = [False] * len(episodes)
    full_episodes = tuple(episode for _, episode, _ in pending)
    mode = update_mode or "replace"
    result = await memory.retain_many(full_episodes, update_mode=mode)
    if not result.accepted or result.items_count != len(full_episodes):
        raise MemoryClientError("Hindsight did not accept the Episode batch")
    await _save_receipts(receipts, full_episodes)
    for index, *_ in pending:
        created[index] = True
    return tuple(created)


def _optional_string(payload: dict[str, Any], name: str) -> str | None:
    value = payload.get(name)
    if value is not None and not isinstance(value, str):
        raise MemoryClientError("Hindsight recall response is malformed")
    return value
