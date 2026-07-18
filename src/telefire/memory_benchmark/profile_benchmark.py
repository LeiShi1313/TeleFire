from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
import hashlib
import json
from math import ceil
import random
from statistics import fmean, median
from typing import Any, Literal

from telefire.memory_benchmark.source import (
    EpisodeEvent,
    SourceCorpus,
    SourceDocument,
)


IngestionProfile = Literal["conversation", "atomic", "timeline", "reference"]
INGESTION_PROFILES: tuple[IngestionProfile, ...] = (
    "conversation",
    "atomic",
    "timeline",
    "reference",
)
AGENT_MEMORY_CONTEXT_CHARS = 4_000
AGENT_MEMORY_ITEMS = 50


@dataclass(frozen=True, slots=True)
class ProfileBenchmarkResult:
    profiles: tuple[str, ...]
    cases: tuple[dict[str, Any], ...]


def profile_config(profile: IngestionProfile) -> dict[str, Any]:
    if profile == "conversation":
        return {
            "retain_extraction_mode": "concise",
            "enable_observations": True,
        }
    if profile == "atomic":
        return {
            "retain_extraction_mode": "concise",
            "enable_observations": False,
        }
    if profile == "timeline":
        return {
            "retain_extraction_mode": "verbatim",
            "enable_observations": False,
        }
    if profile == "reference":
        return {
            "retain_extraction_mode": "chunks",
            "enable_observations": False,
        }
    raise ValueError(f"Unsupported ingestion profile: {profile}")


def prepare_profile_corpus(
    corpus: SourceCorpus,
    profile: IngestionProfile,
    *,
    session_idle_gap: timedelta = timedelta(minutes=15),
    session_max_span: timedelta = timedelta(hours=1),
    session_max_events: int = 30,
    session_max_chars: int = 4_000,
) -> SourceCorpus:
    if profile not in INGESTION_PROFILES:
        raise ValueError(f"Unsupported ingestion profile: {profile}")
    events = tuple(
        sorted(
            corpus.events,
            key=lambda event: (_event_time(event), event.source_id),
        )
    )
    if profile == "conversation":
        groups = _conversation_groups(
            events,
            idle_gap=session_idle_gap,
            max_span=session_max_span,
            max_events=session_max_events,
            max_chars=session_max_chars,
        )
        documents = tuple(
            _conversation_document(corpus, group, index)
            for index, group in enumerate(groups, start=1)
        )
    else:
        context_kind = {
            "atomic": "source item",
            "timeline": "timeline item",
            "reference": "reference item",
        }[profile]
        documents = tuple(
            _verbatim_document(corpus, document, context_kind)
            for document in corpus.documents
        )
    return SourceCorpus(
        bank_id=corpus.bank_id,
        bank_name=corpus.bank_name,
        exported_at=corpus.exported_at,
        documents=documents,
    )


def corpus_event_digest(corpus: SourceCorpus) -> str:
    serialized = json.dumps(
        [
            asdict(event)
            for event in sorted(
                corpus.events,
                key=lambda item: (item.source_id, item.occurred_at, item.text),
            )
        ],
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def sample_corpus(corpus: SourceCorpus, *, documents: int) -> SourceCorpus:
    if documents < 1:
        raise ValueError("Sample document count must be positive")
    if documents >= len(corpus.documents):
        return corpus
    indexes = sorted(
        {
            round(index * (len(corpus.documents) - 1) / (documents - 1))
            for index in range(documents)
        }
        if documents > 1
        else {0}
    )
    if len(indexes) != documents:
        raise AssertionError("Deterministic corpus sampling produced duplicate indexes")
    return SourceCorpus(
        bank_id=corpus.bank_id,
        bank_name=corpus.bank_name,
        exported_at=corpus.exported_at,
        documents=tuple(corpus.documents[index] for index in indexes),
    )


def summarize_profile_benchmark(
    result: ProfileBenchmarkResult,
) -> dict[str, dict[str, float | int | None]]:
    summary: dict[str, dict[str, float | int | None]] = {}
    for profile in result.profiles:
        rows = [row for row in result.cases if profile in row.get("grades", {})]
        grades = [row["grades"][profile] for row in rows]
        measurements = [row["measurements"][profile] for row in rows]
        coverage = [float(grade["answer_coverage"]) for grade in grades]
        attribution = [float(grade["attribution"]) for grade in grades]
        temporal = [
            float(grade["temporal"])
            for grade in grades
            if grade.get("temporal") is not None
        ]
        latencies = sorted(float(item["elapsed_ms"]) for item in measurements)
        summary[profile] = {
            "cases": len(rows),
            "mean_coverage": fmean(coverage) if coverage else 0.0,
            "success_rate": (
                sum(value >= 3 for value in coverage) / len(coverage)
                if coverage
                else 0.0
            ),
            "mean_attribution": fmean(attribution) if attribution else 0.0,
            "mean_temporal": fmean(temporal) if temporal else None,
            "contradiction_rate": (
                sum(bool(grade["contradiction"]) for grade in grades) / len(grades)
                if grades
                else 0.0
            ),
            "latency_p50_ms": median(latencies) if latencies else 0.0,
            "latency_p95_ms": _percentile(latencies, 0.95),
            "mean_context_chars": (
                fmean(
                    len(str(item.get("agent_context", item.get("raw_context")) or ""))
                    for item in measurements
                )
                if measurements
                else 0.0
            ),
        }
    return summary


def compare_profiles(
    result: ProfileBenchmarkResult,
    left: str,
    right: str,
    *,
    bootstrap_samples: int = 20_000,
) -> dict[str, float | int]:
    rows = [
        row
        for row in result.cases
        if left in row.get("grades", {}) and right in row.get("grades", {})
    ]
    coverage_differences = [
        float(row["grades"][left]["answer_coverage"])
        - float(row["grades"][right]["answer_coverage"])
        for row in rows
    ]
    success_differences = [
        float(row["grades"][left]["answer_coverage"] >= 3)
        - float(row["grades"][right]["answer_coverage"] >= 3)
        for row in rows
    ]
    coverage_interval = _bootstrap_mean_interval(
        coverage_differences,
        samples=bootstrap_samples,
    )
    success_interval = _bootstrap_mean_interval(
        success_differences,
        samples=bootstrap_samples,
    )
    return {
        "cases": len(rows),
        "left_wins": sum(value > 0 for value in coverage_differences),
        "right_wins": sum(value < 0 for value in coverage_differences),
        "ties": sum(value == 0 for value in coverage_differences),
        "coverage_difference": (
            fmean(coverage_differences) if coverage_differences else 0.0
        ),
        "coverage_ci_low": coverage_interval[0],
        "coverage_ci_high": coverage_interval[1],
        "success_difference": (
            fmean(success_differences) if success_differences else 0.0
        ),
        "success_ci_low": success_interval[0],
        "success_ci_high": success_interval[1],
    }


def render_agent_context(records: tuple[Any, ...]) -> str:
    if not records:
        return ""
    lines = ["Relevant evidence recalled from the selected memory scope:"]
    for record in records[:AGENT_MEMORY_ITEMS]:
        details = []
        if record.memory_type:
            details.append(record.memory_type)
        if record.occurred_start:
            occurred = (
                f"{record.occurred_start} to {record.occurred_end}"
                if record.occurred_end
                and record.occurred_end != record.occurred_start
                else record.occurred_start
            )
            details.append(f"occurred {occurred}")
        if record.mentioned_at:
            details.append(f"mentioned {record.mentioned_at}")
        if record.entities:
            details.append(f"entities: {', '.join(record.entities)}")
        if record.source_document_ids:
            details.append(f"source: {record.source_document_ids[0]}")
        detail_text = "; ".join(details)[:1_500]
        suffix = (
            f" ({detail_text + '; ' if detail_text else ''}"
            f"memory_id: {record.memory_id})"
        )
        used = len("\n".join(lines)) + 1
        text_budget = AGENT_MEMORY_CONTEXT_CHARS - used - 2 - len(suffix)
        if text_budget < 4:
            break
        memory_text = record.text
        if len(memory_text) > text_budget:
            memory_text = f"{memory_text[: text_budget - 3]}..."
        lines.append(f"- {memory_text}{suffix}")
    return "\n".join(lines)


def _conversation_groups(
    events: tuple[EpisodeEvent, ...],
    *,
    idle_gap: timedelta,
    max_span: timedelta,
    max_events: int,
    max_chars: int,
) -> tuple[tuple[EpisodeEvent, ...], ...]:
    if max_events < 1 or max_chars < 1:
        raise ValueError("Conversation session bounds must be positive")

    events_by_id = {event.source_id: event for event in events}
    reply_groups: dict[str, list[EpisodeEvent]] = {}
    for event in events:
        reply_groups.setdefault(_reply_root(event, events_by_id), []).append(event)

    candidates = sorted(
        (
            tuple(sorted(group, key=lambda event: (_event_time(event), event.source_id)))
            for group in reply_groups.values()
        ),
        key=lambda group: (_event_time(group[0]), group[0].source_id),
    )
    groups: list[list[EpisodeEvent]] = []
    current: list[EpisodeEvent] = []
    for candidate in candidates:
        if current and not _session_accepts_group(
            current,
            candidate,
            idle_gap=idle_gap,
            max_span=max_span,
            max_events=max_events,
            max_chars=max_chars,
        ):
            groups.append(current)
            current = []
        current.extend(candidate)
    if current:
        groups.append(current)
    return tuple(tuple(group) for group in groups)


def _reply_root(
    event: EpisodeEvent,
    events_by_id: dict[str, EpisodeEvent],
) -> str:
    current = event
    visited = {event.source_id}
    while current.reply_to_source_id:
        parent_id = current.reply_to_source_id
        if parent_id in visited:
            return event.source_id
        parent = events_by_id.get(parent_id)
        if parent is None:
            return parent_id
        visited.add(parent_id)
        current = parent
    return current.source_id


def _session_accepts_group(
    current: list[EpisodeEvent],
    candidate: tuple[EpisodeEvent, ...],
    *,
    idle_gap: timedelta,
    max_span: timedelta,
    max_events: int,
    max_chars: int,
) -> bool:
    current_start = _event_time(current[0])
    current_end = _event_time(current[-1])
    candidate_start = _event_time(candidate[0])
    candidate_end = _event_time(candidate[-1])
    return (
        candidate_start - current_end <= idle_gap
        and max(current_end, candidate_end) - min(current_start, candidate_start)
        <= max_span
        and len(current) + len(candidate) <= max_events
        and sum(len(event.text) for event in (*current, *candidate)) <= max_chars
    )


def _conversation_document(
    corpus: SourceCorpus,
    events: tuple[EpisodeEvent, ...],
    index: int,
) -> SourceDocument:
    payload = {
        "schema": "telefire.memory.episode.v1",
        "scope": {
            "id": corpus.bank_id,
            "display_name": corpus.bank_name,
        },
        "events": [_event_payload(event) for event in events],
    }
    content = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    first = events[0].source_id.rsplit(":", 1)[-1]
    return SourceDocument(
        document_id=f"benchmark:conversation-session:{index:04d}:{first}",
        content=content,
        context=f"telegram conversation in {corpus.bank_name}",
        timestamp=events[-1].occurred_at,
        content_hash=hashlib.sha256(content.encode("utf-8")).hexdigest(),
        events=events,
    )


def _verbatim_document(
    corpus: SourceCorpus,
    source_document: SourceDocument,
    context_kind: str,
) -> SourceDocument:
    content = "\n\n".join(event.text for event in source_document.events)
    return SourceDocument(
        document_id=source_document.document_id,
        content=content,
        context=f"telegram {context_kind} in {corpus.bank_name}",
        timestamp=source_document.events[-1].occurred_at,
        content_hash=hashlib.sha256(content.encode("utf-8")).hexdigest(),
        events=source_document.events,
    )


def _event_payload(event: EpisodeEvent) -> dict[str, Any]:
    return {
        "source_id": event.source_id,
        "actor": {
            "id": event.actor_id,
            "display_name": event.actor_name,
        },
        "occurred_at": event.occurred_at,
        "mentioned_at": event.mentioned_at,
        "reply_to_source_id": event.reply_to_source_id,
        "mentioned_actors": [],
        "metadata": {},
        "text": event.text,
    }


def _event_time(event: EpisodeEvent) -> datetime:
    parsed = datetime.fromisoformat(event.occurred_at.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _percentile(values: list[float], quantile: float) -> float:
    if not values:
        return 0.0
    index = max(0, ceil(quantile * len(values)) - 1)
    return values[index]


def _bootstrap_mean_interval(
    values: list[float],
    *,
    samples: int,
) -> tuple[float, float]:
    if not values:
        return (0.0, 0.0)
    generator = random.Random(20260718)
    estimates = sorted(
        fmean(generator.choices(values, k=len(values))) for _ in range(samples)
    )
    return (
        estimates[int(samples * 0.025)],
        estimates[int(samples * 0.975)],
    )
