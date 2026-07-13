from __future__ import annotations

import argparse
import asyncio
import json
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from telefire.ai import AIStateRepository
from telefire.ai_memory import (
    HindsightMemoryClient,
    MemoryClientError,
    MemoryEpisode,
    MemoryEvent,
    MemoryReceiptStore,
)


_MAX_LEGACY_DOCUMENTS = 100_000


@dataclass(frozen=True, slots=True)
class LegacyObservation:
    record_id: str
    subject_id: str
    scope_id: str
    text: str
    occurred_at: datetime
    metadata: dict[str, Any]


@dataclass(frozen=True, slots=True)
class LegacySuppression:
    record_id: str
    record_type: str
    subject_id: str
    scope_id: str
    text: str
    occurred_at: datetime
    source_observation_id: str


@dataclass(slots=True)
class MigrationReport:
    dry_run: bool
    source_documents: int = 0
    source_embedding_model: str | None = None
    source_embedding_dimension: int | None = None
    examined: int = 0
    accepted: int = 0
    unchanged: int = 0
    failed: int = 0
    labels: int = 0
    recoverable_suppressions: int = 0
    invalidated: int = 0
    skipped: Counter[str] = field(default_factory=Counter)
    scopes: dict[str, dict[str, int]] = field(default_factory=dict)
    failures: dict[str, list[str]] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["skipped"] = dict(sorted(self.skipped.items()))
        return payload


@dataclass(frozen=True, slots=True)
class LegacyInventory:
    observations: tuple[LegacyObservation, ...]
    suppressions: tuple[LegacySuppression, ...]
    identities: dict[str, str]
    report: MigrationReport


def inventory_legacy_store(
    source: Path,
) -> LegacyInventory:
    try:
        import zvec
    except ImportError as exc:
        raise RuntimeError(
            "Legacy migration requires the legacy-migration optional dependency"
        ) from exc

    source = Path(source)
    manifest_path = source / "manifest.json"
    collection_path = source / "records"
    if not manifest_path.is_file() or not collection_path.is_dir():
        raise ValueError("Legacy Zvec source is incomplete")
    manifest = json.loads(manifest_path.read_text())
    if not isinstance(manifest, dict) or manifest.get("schema_version") != 1:
        raise ValueError("Legacy Zvec manifest is unsupported")
    identities = _read_identities(source / "identities.json")
    collection = zvec.open(str(collection_path))
    count = int(collection.stats.doc_count)
    if count < 0:
        raise ValueError("Legacy Zvec document count is invalid")
    if count > _MAX_LEGACY_DOCUMENTS:
        raise ValueError(
            "Legacy Zvec source contains more than 100000 documents; "
            "migration refuses a partial inventory"
        )
    docs = list(collection.query(topk=max(1, count))) if count else []
    if len(docs) != count:
        raise RuntimeError(
            "Legacy Zvec query returned an incomplete inventory; migration aborted"
        )
    embedding_model = manifest.get("embedding_model")
    embedding_dimension = manifest.get("embedding_dimension")
    report = MigrationReport(
        dry_run=True,
        source_documents=count,
        source_embedding_model=(
            embedding_model if isinstance(embedding_model, str) else None
        ),
        source_embedding_dimension=(
            embedding_dimension
            if isinstance(embedding_dimension, int)
            and not isinstance(embedding_dimension, bool)
            and embedding_dimension > 0
            else None
        ),
        examined=len(docs),
    )
    observations: list[LegacyObservation] = []
    suppressed_docs: list[tuple[Any, dict[str, Any]]] = []
    for doc in docs:
        fields = doc.fields
        if not isinstance(fields, dict):
            report.skipped["malformed_record"] += 1
            continue
        record_type = fields.get("record_type")
        if record_type in {"fact", "episode"} and fields.get("suppressed") is True:
            suppressed_docs.append((doc.id, fields))
            continue
        if record_type != "observation":
            report.skipped[_skip_reason(record_type)] += 1
            continue
        try:
            observations.append(_observation(doc.id, fields))
        except (TypeError, ValueError, json.JSONDecodeError):
            report.skipped["malformed_observation"] += 1

    observations_by_id = {
        observation.record_id: observation for observation in observations
    }
    suppressions: list[LegacySuppression] = []
    for record_id, fields in suppressed_docs:
        try:
            suppressions.append(_suppression(record_id, fields, observations_by_id))
        except (TypeError, ValueError):
            report.skipped["unrecoverable_suppressed_derived"] += 1
    report.recoverable_suppressions = len(suppressions)

    for item in [*observations, *suppressions]:
        scope = report.scopes.setdefault(
            item.scope_id,
            {
                "examined": 0,
                "accepted": 0,
                "unchanged": 0,
                "failed": 0,
                "invalidated": 0,
            },
        )
        scope["examined"] += 1
    return LegacyInventory(
        observations=tuple(observations),
        suppressions=tuple(suppressions),
        identities=identities,
        report=report,
    )


async def migrate_legacy_store(
    *,
    source: Path,
    hindsight_url: str,
    state_path: Path,
    execute: bool,
    batch_size: int = 10,
) -> MigrationReport:
    if batch_size < 1 or batch_size > 100:
        raise ValueError("Migration batch size must be between 1 and 100")
    inventory = inventory_legacy_store(source)
    observations = inventory.observations
    suppressions = inventory.suppressions
    identities = inventory.identities
    report = inventory.report
    report.dry_run = not execute
    if not execute:
        report.labels = len(identities)
        return report

    store = await AIStateRepository(state_path).connect()
    memory = HindsightMemoryClient(hindsight_url, timeout=300)
    try:
        grouped: dict[str, list[LegacyObservation]] = defaultdict(list)
        for observation in observations:
            grouped[observation.scope_id].append(observation)
        grouped_suppressions: dict[str, list[LegacySuppression]] = defaultdict(list)
        for suppression in suppressions:
            grouped_suppressions[suppression.scope_id].append(suppression)
        scope_ids = sorted(set(grouped) | set(grouped_suppressions))
        for scope_id in scope_ids:
            scoped = grouped[scope_id]
            scoped_suppressions = grouped_suppressions[scope_id]
            actor_labels = {
                subject_id: identities[subject_id]
                for subject_id in {
                    *(observation.subject_id for observation in scoped),
                    *(suppression.subject_id for suppression in scoped_suppressions),
                }
                if subject_id in identities
            }
            await store.record_memory_labels(
                scope_id,
                identities.get(scope_id),
                actor_labels,
            )
            report.labels += bool(identities.get(scope_id)) + len(actor_labels)
            episodes = tuple(
                _legacy_episode(observation, identities)
                for observation in sorted(
                    scoped,
                    key=lambda item: (item.occurred_at, item.record_id),
                )
            )
            for start in range(0, len(episodes), batch_size):
                batch = episodes[start : start + batch_size]
                try:
                    created = await _retain_exact_documents(memory, batch)
                    await _save_receipts(store, batch)
                except Exception as exc:
                    _record_failure(
                        report,
                        scope_id,
                        count=len(batch),
                        reason=(
                            f"{type(exc).__name__}: batch "
                            f"{start // batch_size + 1} failed"
                        ),
                    )
                    continue
                _record_delivery(report, scope_id, created)

            for index, suppression in enumerate(
                sorted(
                    scoped_suppressions,
                    key=lambda item: (item.occurred_at, item.record_id),
                ),
                start=1,
            ):
                episode = _legacy_suppression_episode(suppression, identities)
                try:
                    previous = await store.get_memory_document_receipt(
                        scope_id,
                        episode.document_id,
                    )
                    revision_completed = (
                        previous is not None
                        and previous.content_hash == episode.content_hash
                    )
                    (created,) = await _retain_exact_documents(memory, (episode,))
                    invalidated_count = 0
                    if created or not revision_completed:
                        revised = await memory.revise(
                            scope_id=scope_id,
                            subject_id=suppression.subject_id,
                            instruction=_suppression_instruction(suppression),
                        )
                        invalidated_count = revised.invalidated_count
                    await _save_receipts(store, (episode,))
                except Exception as exc:
                    _record_failure(
                        report,
                        scope_id,
                        count=1,
                        reason=f"{type(exc).__name__}: suppression {index} failed",
                    )
                    continue
                _record_delivery(report, scope_id, (created,))
                report.invalidated += invalidated_count
                report.scopes[scope_id]["invalidated"] += invalidated_count
    finally:
        await memory.close()
        await store.close()
    return report


async def _retain_exact_documents(
    memory: HindsightMemoryClient,
    episodes: tuple[MemoryEpisode, ...],
) -> tuple[bool, ...]:
    destination_content = await asyncio.gather(
        *(
            memory.get_document_content(
                scope_id=episode.scope_id,
                document_id=episode.document_id,
            )
            for episode in episodes
        )
    )
    pending = tuple(
        (index, episode)
        for index, (episode, content) in enumerate(
            zip(episodes, destination_content, strict=True)
        )
        if content != episode.content
    )
    created = [False] * len(episodes)
    if not pending:
        return tuple(created)
    pending_episodes = tuple(episode for _, episode in pending)
    result = await memory.retain_many(pending_episodes, update_mode="replace")
    if not result.accepted or result.items_count != len(pending_episodes):
        raise MemoryClientError("Hindsight did not accept the migration batch")
    for index, _ in pending:
        created[index] = True
    return tuple(created)


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


def _record_delivery(
    report: MigrationReport,
    scope_id: str,
    created: tuple[bool, ...],
) -> None:
    accepted = sum(created)
    unchanged = len(created) - accepted
    report.accepted += accepted
    report.unchanged += unchanged
    report.scopes[scope_id]["accepted"] += accepted
    report.scopes[scope_id]["unchanged"] += unchanged


def _record_failure(
    report: MigrationReport,
    scope_id: str,
    *,
    count: int,
    reason: str,
) -> None:
    report.failed += count
    report.scopes[scope_id]["failed"] += count
    report.failures.setdefault(scope_id, []).append(reason[:500])


def _skip_reason(record_type: Any) -> str:
    if record_type in {"fact", "episode"}:
        return "derived_memory"
    if record_type == "profile":
        return "unrecoverable_profile_only_state"
    return "unknown_record_type"


def _read_identities(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    payload = json.loads(path.read_text())
    if not isinstance(payload, dict):
        raise ValueError("Legacy identity metadata is malformed")
    return {
        key: value[:256]
        for key, value in payload.items()
        if isinstance(key, str) and key and isinstance(value, str) and value.strip()
    }


def _observation(record_id: Any, fields: dict[str, Any]) -> LegacyObservation:
    required = {
        "record_id": record_id,
        "subject_id": fields.get("subject_id"),
        "scope_id": fields.get("scope_id"),
        "text": fields.get("text"),
    }
    if any(not isinstance(value, str) or not value for value in required.values()):
        raise ValueError("Legacy observation identity or text is malformed")
    raw_metadata = fields.get("metadata_json") or "{}"
    metadata = json.loads(raw_metadata)
    if not isinstance(metadata, dict):
        raise ValueError("Legacy observation metadata is malformed")
    return LegacyObservation(
        record_id=record_id,
        subject_id=fields["subject_id"],
        scope_id=fields["scope_id"],
        text=fields["text"],
        occurred_at=_legacy_timestamp(fields.get("occurred_at_ms")),
        metadata=_bounded_metadata(metadata),
    )


def _suppression(
    record_id: Any,
    fields: dict[str, Any],
    observations: dict[str, LegacyObservation],
) -> LegacySuppression:
    required = {
        "record_id": record_id,
        "record_type": fields.get("record_type"),
        "subject_id": fields.get("subject_id"),
        "scope_id": fields.get("scope_id"),
        "text": fields.get("text"),
        "source_observation_id": fields.get("source_observation_id"),
    }
    if any(not isinstance(value, str) or not value for value in required.values()):
        raise ValueError("Legacy suppression identity or text is malformed")
    if fields["record_type"] not in {"fact", "episode"}:
        raise ValueError("Legacy suppression type is malformed")
    source = observations.get(fields["source_observation_id"])
    if (
        source is None
        or source.subject_id != fields["subject_id"]
        or source.scope_id != fields["scope_id"]
    ):
        raise ValueError("Legacy suppression source observation is unavailable")
    return LegacySuppression(
        record_id=record_id,
        record_type=fields["record_type"],
        subject_id=fields["subject_id"],
        scope_id=fields["scope_id"],
        text=fields["text"],
        occurred_at=_legacy_timestamp(fields.get("occurred_at_ms")),
        source_observation_id=fields["source_observation_id"],
    )


def _legacy_timestamp(value: Any) -> datetime:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("Legacy record timestamp is malformed")
    return datetime.fromtimestamp(value / 1000, UTC)


def _bounded_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in list(metadata.items())[:20]:
        if not isinstance(key, str) or not key:
            continue
        if isinstance(value, str):
            result[key[:64]] = value[:500]
        elif value is None or isinstance(value, (bool, int, float)):
            result[key[:64]] = value
    return result


def _legacy_episode(
    observation: LegacyObservation,
    identities: dict[str, str],
) -> MemoryEpisode:
    return MemoryEpisode(
        scope_id=observation.scope_id,
        scope_display_name=identities.get(observation.scope_id),
        document_id=f"legacy:zvec:{observation.record_id}",
        source="legacy-zvec",
        events=(
            MemoryEvent(
                source_id=f"legacy:zvec:{observation.record_id}",
                actor_id=observation.subject_id,
                actor_display_name=identities.get(observation.subject_id),
                occurred_at=observation.occurred_at,
                text=observation.text,
                metadata={
                    "migration": "zvec-v1",
                    "legacy_record_id": observation.record_id,
                    **observation.metadata,
                },
            ),
        ),
    )


def _legacy_suppression_episode(
    suppression: LegacySuppression,
    identities: dict[str, str],
) -> MemoryEpisode:
    document_id = f"legacy:zvec:suppression:{suppression.record_id}"
    return MemoryEpisode(
        scope_id=suppression.scope_id,
        scope_display_name=identities.get(suppression.scope_id),
        document_id=document_id,
        source="legacy-zvec-correction",
        events=(
            MemoryEvent(
                source_id=document_id,
                actor_id="telefire:legacy-migration",
                actor_display_name="Telefire legacy migration",
                occurred_at=suppression.occurred_at,
                text=(
                    f"Legacy owner correction: the derived {suppression.record_type} "
                    "memory was explicitly suppressed and must not be treated as "
                    f"current: {suppression.text}"
                ),
                mentioned_actors=(
                    (
                        suppression.subject_id,
                        identities.get(suppression.subject_id),
                    ),
                ),
                metadata={
                    "migration": "zvec-v1-suppression",
                    "legacy_record_id": suppression.record_id,
                    "legacy_record_type": suppression.record_type,
                    "legacy_source_observation_id": (suppression.source_observation_id),
                },
            ),
        ),
    )


def _suppression_instruction(suppression: LegacySuppression) -> str:
    target = json.dumps(suppression.text, ensure_ascii=False)
    return (
        f"Legacy suppression {suppression.record_id}: invalidate the cited "
        f"{suppression.record_type} memory that exactly matches {target}."
    )


async def _run(args: argparse.Namespace) -> int:
    report = await migrate_legacy_store(
        source=args.source,
        hindsight_url=args.hindsight_url,
        state_path=args.state_path,
        execute=args.execute,
        batch_size=args.batch_size,
    )
    rendered = json.dumps(report.to_dict(), indent=2, sort_keys=True) + "\n"
    if args.report:
        args.report.write_text(rendered)
        args.report.chmod(0o600)
    print(rendered, end="")
    return 1 if report.failed else 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Migrate legacy Zvec observations")
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument(
        "--hindsight-url",
        default="http://127.0.0.1:18888",
    )
    parser.add_argument(
        "--state-path",
        type=Path,
        default=Path.home() / ".telefire" / "ai.db",
    )
    parser.add_argument("--batch-size", type=int, default=10)
    parser.add_argument("--report", type=Path)
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Write Hindsight; without this flag the command is a dry run",
    )
    raise SystemExit(asyncio.run(_run(parser.parse_args())))


if __name__ == "__main__":
    main()
