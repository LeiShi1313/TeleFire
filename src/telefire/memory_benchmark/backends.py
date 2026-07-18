from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
import sqlite3
from time import perf_counter
from typing import Any, Callable
from urllib.parse import quote

import aiohttp

from telefire.memory_benchmark.source import SourceCorpus, SourceDocument


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
    config_updates: dict[str, Any] | None = None,
    require_empty: bool = False,
    verify_corpus: bool = False,
    progress: Callable[[int, int], None] | None = None,
) -> dict[str, Any]:
    if batch_size < 1 or concurrency < 1:
        raise ValueError("batch_size and concurrency must be positive")
    timeout = aiohttp.ClientTimeout(total=900)
    encoded_bank = quote(bank_id, safe="")
    started = perf_counter()
    operations = []
    transient_retries = 0
    async with aiohttp.ClientSession(timeout=timeout) as session:
        await _json_request(
            session,
            "PUT",
            f"{base_url.rstrip('/')}/v1/default/banks/{encoded_bank}",
            json={"name": bank_name},
        )
        if require_empty:
            existing_documents = await _list_hindsight_document_summaries(
                session,
                base_url,
                bank_id,
            )
            existing_stats = await _json_request(
                session,
                "GET",
                f"{base_url.rstrip('/')}/v1/default/banks/{encoded_bank}/stats",
                params={"refresh": "true"},
            )
            existing_nodes = existing_stats.get("total_nodes")
            if not isinstance(existing_nodes, int):
                raise ValueError("Hindsight returned malformed bank stats")
            if existing_documents or existing_nodes:
                raise RuntimeError(
                    f"Benchmark bank {bank_id!r} is not empty; use a fresh bank ID"
                )
        applied_config: dict[str, Any] = {}
        if config_updates:
            config_response = await _json_request(
                session,
                "PATCH",
                f"{base_url.rstrip('/')}/v1/default/banks/{encoded_bank}/config",
                json={"updates": config_updates},
            )
            overrides = config_response.get("overrides")
            if not isinstance(overrides, dict) or overrides != config_updates:
                raise RuntimeError("Hindsight did not apply the requested bank profile")
            applied_config = {
                name: overrides[name] for name in sorted(config_updates)
            }
        batches = [
            corpus.documents[index : index + batch_size]
            for index in range(0, len(corpus.documents), batch_size)
        ]

        async def retain_batch(index: int) -> dict[str, Any]:
            nonlocal transient_retries
            documents = batches[index]
            request = {
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
            }
            for attempt in range(4):
                try:
                    payload = await _json_request(
                        session,
                        "POST",
                        f"{base_url.rstrip('/')}/v1/default/banks/{encoded_bank}/memories",
                        json=request,
                    )
                    break
                except RuntimeError as exc:
                    if attempt >= 3 or not _is_transient_hindsight_write(exc):
                        raise
                    transient_retries += 1
                    await asyncio.sleep(0.25 * (2**attempt))
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

        stats = await wait_for_hindsight_idle(
            session,
            base_url,
            bank_id,
            wait_for_consolidation=(
                config_updates is None
                or config_updates.get("enable_observations") is not False
            ),
        )
        bank_manifest = (
            await _verify_hindsight_bank_state(
                session,
                base_url,
                bank_id,
                corpus,
                config_updates or {},
            )
            if verify_corpus
            else None
        )
    return {
        "backend": "hindsight-fresh",
        "elapsed_seconds": perf_counter() - started,
        "documents": len(corpus.documents),
        "batch_size": batch_size,
        "concurrency": concurrency,
        "config_updates": applied_config,
        "operations": len(operations),
        "transient_retries": transient_retries,
        "stats": stats,
        "bank_manifest": bank_manifest,
    }


async def wait_for_hindsight_idle(
    session: aiohttp.ClientSession,
    base_url: str,
    bank_id: str,
    *,
    timeout_seconds: float = 7_200,
    wait_for_consolidation: bool = True,
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
        pending = int(latest.get("pending_operations", 0))
        if wait_for_consolidation:
            pending += int(latest.get("pending_consolidation", 0))
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


async def verify_hindsight_bank(
    base_url: str,
    bank_id: str,
    corpus: SourceCorpus,
    config_updates: dict[str, Any],
) -> dict[str, Any]:
    timeout = aiohttp.ClientTimeout(total=120)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        return await _verify_hindsight_bank_state(
            session,
            base_url,
            bank_id,
            corpus,
            config_updates,
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
    corpus: SourceCorpus | None = None,
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
        source_documents = _tencent_source_documents(
            connection,
            records_directory,
            corpus,
        )
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
    corpus: SourceCorpus | None,
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
        "SELECT record_id, message_text, timestamp FROM l0_conversations"
    ).fetchall()
    source_pattern = re.compile(r"^\[Source document: ([^]]+)]$", re.MULTILINE)
    expected_documents: dict[tuple[int, str], list[str]] = {}
    if corpus is not None:
        for document in corpus.documents:
            parsed = datetime.fromisoformat(document.timestamp.replace("Z", "+00:00"))
            timestamp_ms = int(parsed.timestamp() * 1_000)
            expected_documents.setdefault(
                (timestamp_ms, _tencent_l0_content(document)), []
            ).append(document.document_id)

    documents_by_source = {}
    for source_id, message_text, timestamp in source_rows:
        embedded = tuple(dict.fromkeys(source_pattern.findall(message_text)))
        if embedded:
            documents_by_source[source_id] = embedded
        elif isinstance(timestamp, int):
            documents_by_source[source_id] = tuple(
                expected_documents.get((timestamp, message_text), ())
            )
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


def _tencent_l0_content(document: SourceDocument) -> str:
    return "\n".join(
        f"[Telegram actor: {event.actor_name} | {event.actor_id}] {event.text}"
        for event in document.events
    )


async def _verify_hindsight_bank_state(
    session: aiohttp.ClientSession,
    base_url: str,
    bank_id: str,
    corpus: SourceCorpus,
    config_updates: dict[str, Any],
) -> dict[str, Any]:
    encoded_bank = quote(bank_id, safe="")
    summaries = await _list_hindsight_document_summaries(
        session,
        base_url,
        bank_id,
    )
    config_payload = await _json_request(
        session,
        "GET",
        f"{base_url.rstrip('/')}/v1/default/banks/{encoded_bank}/config",
    )
    return validate_hindsight_bank_state(
        bank_id=bank_id,
        corpus=corpus,
        config_updates=config_updates,
        document_summaries=summaries,
        config_payload=config_payload,
    )


async def _list_hindsight_document_summaries(
    session: aiohttp.ClientSession,
    base_url: str,
    bank_id: str,
) -> list[dict[str, Any]]:
    encoded_bank = quote(bank_id, safe="")
    summaries: list[dict[str, Any]] = []
    offset = 0
    while True:
        payload = await _json_request(
            session,
            "GET",
            f"{base_url.rstrip('/')}/v1/default/banks/{encoded_bank}/documents",
            params={"limit": "100", "offset": str(offset)},
        )
        items = payload.get("items")
        total = payload.get("total")
        if not isinstance(items, list) or not isinstance(total, int):
            raise ValueError("Hindsight returned a malformed document page")
        if not all(isinstance(item, dict) for item in items):
            raise ValueError("Hindsight returned malformed document summaries")
        summaries.extend(items)
        offset += len(items)
        if offset >= total:
            return summaries
        if not items:
            raise ValueError("Hindsight document pagination stopped early")


def validate_hindsight_bank_state(
    *,
    bank_id: str,
    corpus: SourceCorpus,
    config_updates: dict[str, Any],
    document_summaries: list[dict[str, Any]],
    config_payload: dict[str, Any],
) -> dict[str, Any]:
    expected_documents = {
        document.document_id: document for document in corpus.documents
    }
    if len(expected_documents) != len(corpus.documents):
        raise ValueError("Benchmark corpus contains duplicate document IDs")

    actual_documents: dict[str, dict[str, Any]] = {}
    for summary in document_summaries:
        document_id = _string(summary, "id")
        if document_id in actual_documents:
            raise ValueError(f"Hindsight bank contains duplicate document {document_id}")
        actual_documents[document_id] = summary
    if set(actual_documents) != set(expected_documents):
        missing = len(set(expected_documents) - set(actual_documents))
        stale = len(set(actual_documents) - set(expected_documents))
        raise RuntimeError(
            f"Hindsight bank document set differs from corpus: {missing} missing, "
            f"{stale} stale"
        )

    for document_id, expected in expected_documents.items():
        summary = actual_documents[document_id]
        if _string(summary, "content_hash") != expected.content_hash:
            raise RuntimeError(f"Hindsight document hash differs for {document_id}")
        metadata = summary.get("document_metadata")
        retain = summary.get("retain_params")
        if not isinstance(metadata, dict) or not isinstance(retain, dict):
            raise ValueError(f"Hindsight document metadata is malformed for {document_id}")
        if (
            metadata.get("content_hash") != expected.content_hash
            or metadata.get("scope_id") != corpus.bank_id
            or retain.get("context") != expected.context
        ):
            raise RuntimeError(f"Hindsight document metadata differs for {document_id}")

    overrides = config_payload.get("overrides")
    effective = config_payload.get("config")
    if not isinstance(overrides, dict) or not isinstance(effective, dict):
        raise ValueError("Hindsight returned malformed bank configuration")
    if overrides != config_updates or any(
        effective.get(name) != value for name, value in config_updates.items()
    ):
        raise RuntimeError("Hindsight bank configuration differs from benchmark profile")

    serialized_documents = json.dumps(
        sorted(
            (document_id, document.content_hash)
            for document_id, document in expected_documents.items()
        ),
        separators=(",", ":"),
    )
    return {
        "bank_id": bank_id,
        "documents": len(expected_documents),
        "document_manifest_sha256": hashlib.sha256(
            serialized_documents.encode("utf-8")
        ).hexdigest(),
        "config_overrides": dict(sorted(overrides.items())),
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


def _is_transient_hindsight_write(exc: RuntimeError) -> bool:
    message = str(exc).lower()
    return "deadlock detected" in message or "serialization failure" in message
