from __future__ import annotations

from datetime import UTC, datetime
import json
import sqlite3

from telefire.memory_benchmark.backends import (
    MemoryRecord,
    parse_tencent_search,
    read_tencent_memories,
)
from telefire.memory_benchmark.evaluation import (
    parse_json_object,
    sample_memory_records,
    validate_recall_case,
)
from telefire.memory_benchmark.reporting import summarize_quality
from telefire.memory_benchmark.source import (
    SourceCorpus,
    parse_source_document,
    tencent_seed_payload,
)


def test_episode_export_preserves_actor_time_and_source_boundaries():
    content = json.dumps(
        {
            "schema": "telefire.memory.episode.v1",
            "scope": {"id": "telegram:chat:1", "display_name": "Test Chat"},
            "events": [
                {
                    "source_id": "telegram:message:1:11",
                    "reply_to_source_id": "telegram:message:1:10",
                    "actor": {
                        "id": "telegram:user:7",
                        "display_name": "Alice",
                    },
                    "text": "下周二改为线上会议",
                    "occurred_at": "2026-07-15T08:30:00Z",
                }
            ],
        },
        ensure_ascii=False,
    )
    document = parse_source_document(
        {
            "id": "telegram:thread:1:11",
            "original_text": content,
            "retain_params": {
                "context": "telegram conversation in Test Chat",
                "event_date": "2026-07-15T08:30:00Z",
            },
            "document_metadata": {"content_hash": "content-hash"},
        }
    )

    assert document.document_id == "telegram:thread:1:11"
    assert document.events[0].actor_id == "telegram:user:7"
    assert document.events[0].reply_to_source_id == "telegram:message:1:10"
    assert document.events[0].mentioned_at == "2026-07-15T08:30:00Z"
    assert document.timestamp == "2026-07-15T08:30:00Z"

    corpus = SourceCorpus(
        bank_id="telegram:chat:1",
        bank_name="Test Chat",
        exported_at=datetime.now(UTC).isoformat(),
        documents=(document,),
    )
    payload = tencent_seed_payload(corpus)

    session = payload["sessions"][0]
    assert session["sessionId"] == "benchmark:telegram:chat:1"
    message = session["conversations"][0][0]
    assert message == {
        "role": "user",
        "content": (
            "[Source document: telegram:thread:1:11]\n"
            "[2026-07-15T08:30:00Z] [Telegram actor: Alice | "
            "telegram:user:7] 下周二改为线上会议"
        ),
        "timestamp": "2026-07-15T08:30:00Z",
    }


def test_tencent_search_and_store_are_normalized_to_common_records(tmp_path):
    results = parse_tencent_search(
        """Found 1 matching memories:

- **[episodic]** (priority: 75) [scene: 发布计划] (score: 0.032)
  Alice 将发布时间改到了周二。
"""
    )

    assert len(results) == 1
    assert results[0].text == "Alice 将发布时间改到了周二。"
    assert results[0].memory_type == "episodic"
    assert results[0].scene_name == "发布计划"

    database = tmp_path / "vectors.db"
    connection = sqlite3.connect(database)
    connection.execute(
        """
        CREATE TABLE l1_records (
            record_id TEXT, content TEXT, type TEXT, scene_name TEXT,
            session_id TEXT, timestamp_start TEXT, timestamp_end TEXT,
            created_time TEXT
        )
        """
    )
    connection.execute(
        "INSERT INTO l1_records VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "memory-1",
            "Alice 将发布时间改到了周二。",
            "episodic",
            "发布计划",
            "telegram:thread:1:11",
            "2026-07-15T08:30:00Z",
            "2026-07-15T08:30:00Z",
            "2026-07-15T09:00:00Z",
        ),
    )
    connection.execute(
        """
        CREATE TABLE l0_conversations (
            record_id TEXT PRIMARY KEY, message_text TEXT NOT NULL
        )
        """
    )
    connection.execute(
        "INSERT INTO l0_conversations VALUES (?, ?)",
        (
            "source-message-1",
            "[Source document: telegram:thread:1:11]\n"
            "[2026-07-15T08:30:00Z] [Telegram actor: Alice | telegram:user:7] "
            "下周二改为线上会议",
        ),
    )
    connection.commit()
    connection.close()

    records_directory = tmp_path / "records"
    records_directory.mkdir()
    (records_directory / "2026-07-15.jsonl").write_text(
        json.dumps(
            {
                "id": "memory-1",
                "content": "Alice 将发布时间改到了周二。",
                "source_message_ids": ["source-message-1"],
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    stored = read_tencent_memories(database, records_directory=records_directory)
    assert stored[0].source_document_ids == ("telegram:thread:1:11",)
    assert stored[0].scene_name == "发布计划"


def test_source_corpus_loading_does_not_mutate_payload():
    payload = {
        "schema": "telefire.memory-benchmark.source.v1",
        "bank_id": "telegram:chat:1",
        "bank_name": "Test Chat",
        "exported_at": "2026-07-15T09:00:00+00:00",
        "documents": [
            {
                "document_id": "telegram:thread:1:11",
                "content": "{}",
                "context": "test",
                "timestamp": "2026-07-15T08:30:00Z",
                "content_hash": "hash",
                "events": [
                    {
                        "source_id": "telegram:message:1:11",
                        "actor_id": "telegram:user:7",
                        "actor_name": "Alice",
                        "text": "下周二改为线上会议",
                        "occurred_at": "2026-07-15T08:30:00Z",
                        "mentioned_at": "2026-07-15T08:30:00Z",
                        "reply_to_source_id": None,
                    }
                ],
            }
        ],
    }

    first = SourceCorpus.from_dict(payload)
    second = SourceCorpus.from_dict(payload)

    assert first == second
    assert payload["documents"][0]["events"][0]["actor_name"] == "Alice"


def test_recall_case_requires_verbatim_source_evidence():
    document = parse_source_document(
        {
            "id": "telegram:thread:1:11",
            "original_text": json.dumps(
                {
                    "schema": "telefire.memory.episode.v1",
                    "events": [
                        {
                            "source_id": "telegram:message:1:11",
                            "actor": {
                                "id": "telegram:user:7",
                                "display_name": "Alice",
                            },
                            "text": "下周二改为线上会议",
                            "occurred_at": "2026-07-15T08:30:00Z",
                        }
                    ],
                },
                ensure_ascii=False,
            ),
            "retain_params": {},
            "document_metadata": {},
        }
    )

    valid = validate_recall_case(
        {
            "category": "temporal",
            "question": "Alice 把会议改到了什么时候？",
            "answer": "下周二。",
            "evidence": [
                {
                    "document_id": "telegram:thread:1:11",
                    "quote": "下周二改为线上会议",
                }
            ],
        },
        {document.document_id: document},
    )
    invalid = validate_recall_case(
        {
            "category": "temporal",
            "question": "Alice 把会议改到了什么时候？",
            "answer": "下周三。",
            "evidence": [
                {
                    "document_id": "telegram:thread:1:11",
                    "quote": "下周三改为线上会议",
                }
            ],
        },
        {document.document_id: document},
    )

    assert valid is not None
    assert valid.evidence[0].quote == "下周二改为线上会议"
    assert invalid is None


def test_json_parser_and_memory_sampling_are_deterministic():
    assert parse_json_object('```json\n{"score": 4}\n```') == {"score": 4}
    records = tuple(
        MemoryRecord(
            backend="test",
            memory_id=f"memory-{index}",
            text=str(index),
            memory_type="world" if index % 2 else "observation",
        )
        for index in range(10)
    )

    first = sample_memory_records(records, limit=5)
    second = sample_memory_records(tuple(reversed(records)), limit=5)

    assert [record.memory_id for record in first] == [
        record.memory_id for record in second
    ]
    assert {record.memory_type for record in first} == {"world", "observation"}


def test_quality_summary_keeps_recall_extraction_and_latency_separate():
    quality = {
        "inventories": {
            "hindsight": {"total": 4, "source_linked": 4, "types": {"world": 4}},
            "tencent": {"total": 2, "source_linked": 1, "types": {"episodic": 2}},
        },
        "extraction": {
            "hindsight": {
                "sample_size": 2,
                "grades": [
                    {
                        "faithfulness": 4,
                        "attribution": 4,
                        "specificity": 3,
                        "usefulness": 3,
                        "temporal": None,
                        "unsupported_claim": False,
                        "overcombined": False,
                    },
                    {
                        "faithfulness": 2,
                        "attribution": 1,
                        "specificity": 2,
                        "usefulness": 2,
                        "temporal": 3,
                        "unsupported_claim": True,
                        "overcombined": True,
                    },
                ],
            },
            "tencent": {"sample_size": 0, "grades": []},
        },
        "recall": [
            {
                "case": {"category": "direct"},
                "measurements": {
                    "hindsight": {"elapsed_ms": 100},
                    "tencent": {"elapsed_ms": 20},
                },
                "grades": {
                    "hindsight": {
                        "answer_coverage": 4,
                        "attribution": 4,
                        "contradiction": False,
                    },
                    "tencent": {
                        "answer_coverage": 2,
                        "attribution": 3,
                        "contradiction": True,
                    },
                },
            }
        ],
    }

    summary = summarize_quality(quality)

    assert summary["hindsight"]["recall_success_rate"] == 1.0
    assert summary["hindsight"]["faithfulness"] == 3.0
    assert summary["hindsight"]["unsupported_rate"] == 0.5
    assert summary["hindsight"]["latency_p50_ms"] == 100
    assert summary["tencent"]["recall_success_rate"] == 0.0
    assert summary["tencent"]["source_link_rate"] == 0.5
