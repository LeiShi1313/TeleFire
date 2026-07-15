from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass
import json
from pathlib import Path
import re
import sqlite3
from time import perf_counter
from typing import Any, Callable
from urllib.parse import quote

import aiohttp

from telefire.memory_benchmark.source import SourceCorpus


@dataclass(frozen=True, slots=True)
class MemoryRecord:
    backend: str
    memory_id: str
    text: str
    memory_type: str
    source_document_ids: tuple[str, ...] = ()
    entities: tuple[str, ...] = ()
    occurred_start: str | None = None
    occurred_end: str | None = None
    mentioned_at: str | None = None
    scene_name: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class RecallMeasurement:
    backend: str
    query: str
    elapsed_ms: float
    records: tuple[MemoryRecord, ...]
    raw_context: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "backend": self.backend,
            "query": self.query,
            "elapsed_ms": self.elapsed_ms,
            "records": [record.to_dict() for record in self.records],
            "raw_context": self.raw_context,
        }


async def ingest_hindsight(
    base_url: str,
    bank_id: str,
    bank_name: str,
    corpus: SourceCorpus,
    *,
    batch_size: int = 1,
    concurrency: int = 4,
    progress: Callable[[int, int], None] | None = None,
) -> dict[str, Any]:
    if batch_size < 1 or concurrency < 1:
        raise ValueError("batch_size and concurrency must be positive")
    timeout = aiohttp.ClientTimeout(total=900)
    encoded_bank = quote(bank_id, safe="")
    started = perf_counter()
    operations = []
    async with aiohttp.ClientSession(timeout=timeout) as session:
        await _json_request(
            session,
            "PUT",
            f"{base_url.rstrip('/')}/v1/default/banks/{encoded_bank}",
            json={"name": bank_name},
        )
        batches = [
            corpus.documents[index : index + batch_size]
            for index in range(0, len(corpus.documents), batch_size)
        ]

        async def retain_batch(index: int) -> dict[str, Any]:
            documents = batches[index]
            payload = await _json_request(
                session,
                "POST",
                f"{base_url.rstrip('/')}/v1/default/banks/{encoded_bank}/memories",
                json={
                    "async": False,
                    "items": [
                        {
                            "content": document.content,
                            "context": document.context,
                            "timestamp": document.timestamp,
                            "document_id": document.document_id,
                            "update_mode": "replace",
                            "metadata": {
                                "client": "telefire-memory-benchmark",
                                "source": "telegram",
                                "scope_id": corpus.bank_id,
                                "content_hash": document.content_hash,
                            },
                            "entities": [
                                {"text": actor_id, "type": "PERSON"}
                                for actor_id in sorted(
                                    {event.actor_id for event in document.events}
                                )
                            ],
                        }
                        for document in documents
                    ],
                },
            )
            if payload.get("success") is not True:
                raise RuntimeError(f"Hindsight rejected batch {index}")
            return payload

        completed_documents = 0
        for start in range(0, len(batches), concurrency):
            wave = batches[start : start + concurrency]
            payloads = await asyncio.gather(
                *(retain_batch(index) for index in range(start, start + len(wave)))
            )
            operations.extend(payload.get("operation_id") for payload in payloads)
            completed_documents += sum(len(documents) for documents in wave)
            if progress is not None:
                progress(completed_documents, len(corpus.documents))

        stats = await wait_for_hindsight_idle(session, base_url, bank_id)
    return {
        "backend": "hindsight-fresh",
        "elapsed_seconds": perf_counter() - started,
        "documents": len(corpus.documents),
        "batch_size": batch_size,
        "concurrency": concurrency,
        "operations": len(operations),
        "stats": stats,
    }


async def wait_for_hindsight_idle(
    session: aiohttp.ClientSession,
    base_url: str,
    bank_id: str,
    *,
    timeout_seconds: float = 900,
) -> dict[str, Any]:
    encoded_bank = quote(bank_id, safe="")
    deadline = asyncio.get_running_loop().time() + timeout_seconds
    stable = 0
    latest: dict[str, Any] = {}
    while asyncio.get_running_loop().time() < deadline:
        latest = await _json_request(
            session,
            "GET",
            f"{base_url.rstrip('/')}/v1/default/banks/{encoded_bank}/stats",
            params={"refresh": "true"},
        )
        pending = int(latest.get("pending_operations", 0)) + int(
            latest.get("pending_consolidation", 0)
        )
        failed = int(latest.get("failed_operations", 0)) + int(
            latest.get("failed_consolidation", 0)
        )
        if failed:
            raise RuntimeError(f"Hindsight reported {failed} failed operations")
        stable = stable + 1 if pending == 0 else 0
        if stable >= 3:
            return latest
        await asyncio.sleep(2)
    raise TimeoutError("Hindsight did not become idle")


async def list_hindsight_memories(
    base_url: str,
    bank_id: str,
    *,
    backend: str,
) -> tuple[MemoryRecord, ...]:
    timeout = aiohttp.ClientTimeout(total=120)
    encoded_bank = quote(bank_id, safe="")
    offset = 0
    all_items: list[dict[str, Any]] = []
    async with aiohttp.ClientSession(timeout=timeout) as session:
        while True:
            payload = await _json_request(
                session,
                "GET",
                f"{base_url.rstrip('/')}/v1/default/banks/{encoded_bank}/memories/list",
                params={"limit": "100", "offset": str(offset)},
            )
            page_items = payload.get("items")
            total = payload.get("total")
            if not isinstance(page_items, list) or not isinstance(total, int):
                raise ValueError("Hindsight returned a malformed memory page")
            all_items.extend(page_items)
            offset += len(page_items)
            if offset >= total:
                break
            if not page_items:
                raise ValueError("Hindsight memory pagination stopped early")

        documents_by_memory = {
            _string(item, "id"): document_id
            for item in all_items
            if (document_id := _optional(item, "document_id")) is not None
        }
        observation_ids = [
            _string(item, "id")
            for item in all_items
            if item.get("fact_type") == "observation"
        ]
        semaphore = asyncio.Semaphore(12)

        async def observation_sources(memory_id: str) -> tuple[str, ...]:
            async with semaphore:
                detail = await _json_request(
                    session,
                    "GET",
                    f"{base_url.rstrip('/')}/v1/default/banks/{encoded_bank}/memories/"
                    f"{quote(memory_id, safe='')}",
                )
            source_ids = detail.get("source_memory_ids") or []
            if not isinstance(source_ids, list) or not all(
                isinstance(source_id, str) for source_id in source_ids
            ):
                raise ValueError("Hindsight returned malformed observation sources")
            return tuple(
                dict.fromkeys(
                    documents_by_memory[source_id]
                    for source_id in source_ids
                    if source_id in documents_by_memory
                )
            )

        observation_documents = dict(
            zip(
                observation_ids,
                await asyncio.gather(
                    *(observation_sources(memory_id) for memory_id in observation_ids)
                ),
                strict=True,
            )
        )
    return tuple(
        _hindsight_record(
            backend,
            item,
            inherited_source_documents=observation_documents.get(_string(item, "id"), ()),
        )
        for item in all_items
    )


async def recall_hindsight(
    base_url: str,
    bank_id: str,
    query: str,
    *,
    backend: str,
    max_tokens: int = 2_000,
) -> RecallMeasurement:
    timeout = aiohttp.ClientTimeout(total=120)
    encoded_bank = quote(bank_id, safe="")
    async with aiohttp.ClientSession(timeout=timeout) as session:
        started = perf_counter()
        payload = await _json_request(
            session,
            "POST",
            f"{base_url.rstrip('/')}/v1/default/banks/{encoded_bank}/memories/recall",
            json={
                "query": query,
                "budget": "mid",
                "max_tokens": max_tokens,
                "types": ["world", "experience", "observation"],
                "prefer_observations": True,
                "include": {
                    "entities": {"max_tokens": 500},
                    "source_facts": {"max_tokens": 750},
                },
            },
        )
        elapsed_ms = (perf_counter() - started) * 1_000
    items = payload.get("results")
    if not isinstance(items, list):
        raise ValueError("Hindsight returned malformed recall results")
    records = tuple(_hindsight_recall_record(backend, item) for item in items)
    return RecallMeasurement(
        backend=backend,
        query=query,
        elapsed_ms=elapsed_ms,
        records=records,
        raw_context="\n".join(record.text for record in records),
    )


async def seed_tencent(
    base_url: str,
    seed_data: dict[str, Any],
) -> dict[str, Any]:
    timeout = aiohttp.ClientTimeout(total=10_800)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        started = perf_counter()
        payload = await _json_request(
            session,
            "POST",
            f"{base_url.rstrip('/')}/seed",
            json={
                "data": seed_data,
                "strict_round_role": False,
                "auto_fill_timestamps": False,
            },
        )
    payload["elapsed_seconds_client"] = perf_counter() - started
    return payload


async def recall_tencent(
    base_url: str,
    query: str,
    *,
    limit: int = 10,
) -> RecallMeasurement:
    timeout = aiohttp.ClientTimeout(total=120)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        started = perf_counter()
        payload = await _json_request(
            session,
            "POST",
            f"{base_url.rstrip('/')}/search/memories",
            json={"query": query, "limit": limit},
        )
        elapsed_ms = (perf_counter() - started) * 1_000
    results = payload.get("results")
    if not isinstance(results, str):
        raise ValueError("Tencent returned malformed memory search results")
    return RecallMeasurement(
        backend="tencent-l1",
        query=query,
        elapsed_ms=elapsed_ms,
        records=parse_tencent_search(results),
        raw_context=results,
    )


def parse_tencent_search(value: str) -> tuple[MemoryRecord, ...]:
    records = []
    current_type: str | None = None
    current_scene: str | None = None
    current_id = 0
    header = re.compile(r"^- \*\*\[(?P<type>[^]]+)]\*\*")
    scene = re.compile(r"\[scene: (?P<scene>[^]]+)]")
    for line in value.splitlines():
        match = header.match(line)
        if match:
            current_type = match.group("type")
            scene_match = scene.search(line)
            current_scene = scene_match.group("scene") if scene_match else None
            continue
        if current_type and line.startswith("  ") and line.strip():
            current_id += 1
            records.append(
                MemoryRecord(
                    backend="tencent-l1",
                    memory_id=f"search-result-{current_id}",
                    text=line.strip(),
                    memory_type=current_type,
                    scene_name=current_scene,
                )
            )
            current_type = None
            current_scene = None
    return tuple(records)


def read_tencent_memories(
    database: Path,
    *,
    records_directory: Path | None = None,
) -> tuple[MemoryRecord, ...]:
    uri = f"file:{database.resolve()}?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    try:
        rows = connection.execute(
            """
            SELECT record_id, content, type, scene_name,
                   timestamp_start, timestamp_end
            FROM l1_records
            ORDER BY created_time, record_id
            """
        ).fetchall()
        source_documents = _tencent_source_documents(connection, records_directory)
    finally:
        connection.close()
    return tuple(
        MemoryRecord(
            backend="tencent-l1",
            memory_id=row[0],
            text=row[1],
            memory_type=row[2],
            source_document_ids=source_documents.get(row[0], ()),
            occurred_start=row[4] or None,
            occurred_end=row[5] or None,
            scene_name=row[3] or None,
        )
        for row in rows
    )


def _tencent_source_documents(
    connection: sqlite3.Connection,
    records_directory: Path | None,
) -> dict[str, tuple[str, ...]]:
    if records_directory is None or not records_directory.exists():
        return {}

    memory_sources: dict[str, tuple[str, ...]] = {}
    for path in sorted(records_directory.glob("*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            value = json.loads(line)
            memory_id = value.get("id")
            source_ids = value.get("source_message_ids") or []
            if isinstance(memory_id, str) and all(
                isinstance(source_id, str) for source_id in source_ids
            ):
                memory_sources[memory_id] = tuple(source_ids)

    source_rows = connection.execute(
        "SELECT record_id, message_text FROM l0_conversations"
    ).fetchall()
    source_pattern = re.compile(r"^\[Source document: ([^]]+)]$", re.MULTILINE)
    documents_by_source = {
        source_id: tuple(dict.fromkeys(source_pattern.findall(message_text)))
        for source_id, message_text in source_rows
    }
    return {
        memory_id: tuple(
            dict.fromkeys(
                document_id
                for source_id in source_ids
                for document_id in documents_by_source.get(source_id, ())
            )
        )
        for memory_id, source_ids in memory_sources.items()
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


def _hindsight_record(
    backend: str,
    item: Any,
    *,
    inherited_source_documents: tuple[str, ...] = (),
) -> MemoryRecord:
    if not isinstance(item, dict):
        raise ValueError("Hindsight returned a malformed memory")
    entities = item.get("entities") or ""
    if isinstance(entities, str):
        parsed_entities = tuple(part.strip() for part in entities.split(",") if part.strip())
    elif isinstance(entities, list) and all(isinstance(part, str) for part in entities):
        parsed_entities = tuple(entities)
    else:
        raise ValueError("Hindsight returned malformed memory entities")
    return MemoryRecord(
        backend=backend,
        memory_id=_string(item, "id"),
        text=_string(item, "text"),
        memory_type=_string(item, "fact_type"),
        source_document_ids=(
            (document_id,)
            if (document_id := _optional(item, "document_id"))
            else inherited_source_documents
        ),
        entities=parsed_entities,
        occurred_start=_optional(item, "occurred_start"),
        occurred_end=_optional(item, "occurred_end"),
        mentioned_at=_optional(item, "mentioned_at"),
    )


def _hindsight_recall_record(backend: str, item: Any) -> MemoryRecord:
    if not isinstance(item, dict):
        raise ValueError("Hindsight returned a malformed recalled memory")
    entities = item.get("entities") or []
    if not isinstance(entities, list) or not all(isinstance(part, str) for part in entities):
        raise ValueError("Hindsight returned malformed recalled entities")
    return MemoryRecord(
        backend=backend,
        memory_id=_string(item, "id"),
        text=_string(item, "text"),
        memory_type=_optional(item, "type") or "unknown",
        source_document_ids=(document_id,) if (document_id := _optional(item, "document_id")) else (),
        entities=tuple(entities),
        occurred_start=_optional(item, "occurred_start"),
        occurred_end=_optional(item, "occurred_end"),
        mentioned_at=_optional(item, "mentioned_at"),
    )


def _string(payload: dict[str, Any], name: str) -> str:
    value = payload.get(name)
    if not isinstance(value, str):
        raise ValueError(f"Missing or invalid {name}")
    return value


def _optional(payload: dict[str, Any], name: str) -> str | None:
    value = payload.get(name)
    if value is not None and not isinstance(value, str):
        raise ValueError(f"Invalid {name}")
    return value
