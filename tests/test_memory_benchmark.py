from __future__ import annotations

from datetime import UTC, datetime
import json

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
    assert session["sessionId"] == document.document_id
    message = session["conversations"][0][0]
    assert message == {
        "role": "user",
        "content": "[Telegram actor: Alice | telegram:user:7]\n下周二改为线上会议",
        "timestamp": "2026-07-15T08:30:00Z",
    }
