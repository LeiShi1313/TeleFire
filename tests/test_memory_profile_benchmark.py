from __future__ import annotations

from datetime import UTC, datetime, timedelta
import json

import pytest

from telefire.memory_benchmark import backends
from telefire.memory_benchmark.backends import (
    MemoryRecord,
    validate_hindsight_bank_state,
    wait_for_hindsight_idle,
)
from telefire.memory_benchmark.profile_benchmark import (
    ProfileBenchmarkResult,
    compare_profiles,
    corpus_event_digest,
    prepare_profile_corpus,
    profile_config,
    render_agent_context,
    sample_corpus,
    summarize_profile_benchmark,
)
from telefire.memory_benchmark.source import (
    EpisodeEvent,
    SourceCorpus,
    SourceDocument,
)
from telefire.memory_benchmark.telegram_source import (
    enrich_timeline_rows,
    read_timeline_rows,
    timeline_to_corpus,
    write_timeline_rows,
)


def _document(message_id: int, occurred_at: datetime, text: str) -> SourceDocument:
    timestamp = occurred_at.isoformat().replace("+00:00", "Z")
    event = EpisodeEvent(
        source_id=f"telegram:message:-1001:{message_id}",
        actor_id="telegram:channel:1",
        actor_name="Seele Leaks",
        text=text,
        occurred_at=timestamp,
        mentioned_at=timestamp,
        reply_to_source_id=None,
    )
    return SourceDocument(
        document_id=f"telegram:timeline-item:-1001:{message_id}",
        content=text,
        context="telegram timeline in Seele Leaks",
        timestamp=timestamp,
        content_hash=f"hash-{message_id}",
        events=(event,),
    )


def _corpus() -> SourceCorpus:
    start = datetime(2026, 7, 1, 10, tzinfo=UTC)
    return SourceCorpus(
        bank_id="telegram:chat:-1001",
        bank_name="Seele Leaks",
        exported_at=start.isoformat(),
        documents=(
            _document(1, start, "第一条消息"),
            _document(2, start + timedelta(minutes=5), "第二条消息"),
            _document(3, start + timedelta(minutes=25), "第三条消息"),
        ),
    )


def _timeline_row(
    message_id: int,
    occurred_at: str,
    text: str,
    **updates,
) -> dict:
    row = {
        "platform": "telegram",
        "chat_id": -1001,
        "message_id": message_id,
        "source_id": f"telegram:message:-1001:{message_id}",
        "occurred_at": occurred_at,
        "text": text,
        "sender_id": 1,
        "reply_to_message_id": None,
        "grouped_id": None,
        "post_author": None,
        "has_media": False,
        "media_type": None,
    }
    row.update(updates)
    return row


def test_ingestion_profiles_change_representation_but_not_source_events():
    source = _corpus()

    conversation = prepare_profile_corpus(source, "conversation")
    atomic = prepare_profile_corpus(source, "atomic")
    timeline = prepare_profile_corpus(source, "timeline")
    reference = prepare_profile_corpus(source, "reference")

    assert len(conversation.documents) == 2
    assert [len(document.events) for document in conversation.documents] == [2, 1]
    assert '"schema":"telefire.memory.episode.v1"' in conversation.documents[0].content
    assert atomic.documents[0].content == "第一条消息"
    assert timeline.documents[0].content == "第一条消息"
    assert reference.documents[0].content == "第一条消息"
    assert atomic.events == source.events
    assert timeline.events == source.events
    assert reference.events == source.events


def test_conversation_profile_keeps_a_reply_chain_across_idle_gaps():
    start = datetime(2026, 7, 1, 10, tzinfo=UTC)
    root = _document(1, start, "根消息")
    reply_event = EpisodeEvent(
        source_id="telegram:message:-1001:2",
        actor_id="telegram:channel:1",
        actor_name="Seele Leaks",
        text="两小时后的回复",
        occurred_at=(start + timedelta(hours=2)).isoformat().replace("+00:00", "Z"),
        mentioned_at=(start + timedelta(hours=2)).isoformat().replace("+00:00", "Z"),
        reply_to_source_id=root.events[0].source_id,
    )
    reply = SourceDocument(
        document_id="telegram:timeline-item:-1001:2",
        content=reply_event.text,
        context="telegram timeline in Seele Leaks",
        timestamp=reply_event.occurred_at,
        content_hash="hash-2",
        events=(reply_event,),
    )
    source = SourceCorpus(
        bank_id="telegram:chat:-1001",
        bank_name="Seele Leaks",
        exported_at=start.isoformat(),
        documents=(root, reply),
    )

    conversation = prepare_profile_corpus(source, "conversation")

    assert len(conversation.documents) == 1
    assert conversation.documents[0].events == (root.events[0], reply_event)


def test_profile_config_only_controls_hindsight_ingestion():
    assert profile_config("conversation") == {
        "retain_extraction_mode": "concise",
        "enable_observations": True,
    }
    assert profile_config("atomic") == {
        "retain_extraction_mode": "concise",
        "enable_observations": False,
    }
    assert profile_config("timeline") == {
        "retain_extraction_mode": "verbatim",
        "enable_observations": False,
    }
    assert profile_config("reference") == {
        "retain_extraction_mode": "chunks",
        "enable_observations": False,
    }


def test_timeline_export_keeps_raw_rows_but_only_scores_textual_messages():
    result = timeline_to_corpus(
        channel_id=-1001,
        channel_title="Seele Leaks",
        exported_at="2026-07-01T11:00:00Z",
        rows=(
            _timeline_row(
                2,
                "2026-07-01T10:05:00Z",
                "有文字的图片说明",
                reply_to_message_id=1,
                grouped_id=99,
                has_media=True,
                media_type="image/jpeg",
            ),
            _timeline_row(
                1,
                "2026-07-01T10:00:00Z",
                "",
                grouped_id=99,
                has_media=True,
                media_type="image/jpeg",
            ),
            _timeline_row(
                3,
                "2026-07-01T10:06:00Z",
                "同一相册的第二段说明",
                grouped_id=99,
                has_media=True,
                media_type="image/jpeg",
            ),
        ),
    )

    assert len(result.rows) == 3
    assert len(result.corpus.documents) == 1
    events = result.corpus.documents[0].events
    assert [event.text for event in events] == [
        "有文字的图片说明",
        "同一相册的第二段说明",
    ]
    assert events[0].reply_to_source_id == "telegram:message:-1001:1"
    assert result.stats == {
        "downloaded": 3,
        "textual": 2,
        "with_media": 3,
        "blank_media": 1,
    }


def test_timeline_export_groups_textual_media_album_as_one_source_document():
    result = timeline_to_corpus(
        channel_id=-1001,
        channel_title="Seele Leaks",
        exported_at="2026-07-01T11:00:00Z",
        rows=(
            _timeline_row(
                1,
                "2026-07-01T10:00:00Z",
                "图片一",
                grouped_id=99,
                has_media=True,
            ),
            _timeline_row(
                2,
                "2026-07-01T10:00:01Z",
                "图片二",
                grouped_id=99,
                has_media=True,
            ),
        ),
    )

    assert len(result.corpus.documents) == 1
    assert result.corpus.documents[0].document_id == "telegram:channel-album:-1001:99"
    assert len(result.corpus.documents[0].events) == 2


def test_retained_attachment_description_enriches_frozen_timeline():
    source = _corpus()
    retained_event = EpisodeEvent(
        source_id="telegram:message:-1001:1",
        actor_id="telegram:channel:1",
        actor_name="Seele Leaks",
        text="Generated attachment description: 角色立绘",
        occurred_at="2026-07-01T10:00:00Z",
        mentioned_at="2026-07-01T10:00:00Z",
        reply_to_source_id=None,
    )
    retained = SourceCorpus(
        bank_id=source.bank_id,
        bank_name=source.bank_name,
        exported_at=source.exported_at,
        documents=(
            SourceDocument(
                document_id="retained",
                content=retained_event.text,
                context="retained",
                timestamp=retained_event.occurred_at,
                content_hash="hash",
                events=(retained_event,),
            ),
        ),
    )
    rows = enrich_timeline_rows(
        (
            _timeline_row(
                1,
                "2026-07-01T10:00:00Z",
                "",
                has_media=True,
            ),
        ),
        retained,
    )

    result = timeline_to_corpus(
        channel_id=-1001,
        channel_title="Seele Leaks",
        exported_at="2026-07-01T11:00:00Z",
        rows=rows,
    )

    assert result.corpus.events[0].text == retained_event.text
    assert result.rows[0]["memory_text"] == retained_event.text


def test_timeline_ledger_round_trip_binds_chat_identity_and_digest(tmp_path):
    path = tmp_path / "timeline.jsonl"
    rows = (
        _timeline_row(1, "2026-07-01T10:00:00Z", "第一条"),
        _timeline_row(2, "2026-07-01T10:01:00Z", "第二条"),
    )

    write_timeline_rows(rows, path)

    assert read_timeline_rows(path) == rows
    assert path.stat().st_mode & 0o777 == 0o600

    lines = path.read_text(encoding="utf-8").splitlines()
    tampered = json.loads(lines[2])
    tampered["text"] = "被篡改"
    lines[2] = json.dumps(tampered, ensure_ascii=False)
    path.write_text("\n".join(lines), encoding="utf-8")
    with pytest.raises(ValueError, match="manifest does not match"):
        read_timeline_rows(path)


def test_timeline_enrichment_rejects_a_different_chat():
    row = _timeline_row(1, "2026-07-01T10:00:00Z", "消息")
    row["chat_id"] = -2002
    row["source_id"] = "telegram:message:-2002:1"

    with pytest.raises(ValueError, match="different chats"):
        enrich_timeline_rows((row,), _corpus())


def test_hindsight_bank_manifest_rejects_stale_or_changed_documents():
    corpus = prepare_profile_corpus(_corpus(), "atomic")
    config = profile_config("atomic")
    summaries = [
        {
            "id": document.document_id,
            "content_hash": document.content_hash,
            "document_metadata": {
                "content_hash": document.content_hash,
                "scope_id": corpus.bank_id,
            },
            "retain_params": {"context": document.context},
        }
        for document in corpus.documents
    ]
    config_payload = {"overrides": config, "config": config}

    manifest = validate_hindsight_bank_state(
        bank_id="benchmark:test",
        corpus=corpus,
        config_updates=config,
        document_summaries=summaries,
        config_payload=config_payload,
    )

    assert manifest["documents"] == len(corpus.documents)
    with pytest.raises(RuntimeError, match="stale"):
        validate_hindsight_bank_state(
            bank_id="benchmark:test",
            corpus=corpus,
            config_updates=config,
            document_summaries=[
                *summaries,
                {
                    "id": "stale",
                    "content_hash": "stale",
                    "document_metadata": {},
                    "retain_params": {},
                },
            ],
            config_payload=config_payload,
        )


def test_all_profile_projections_preserve_the_same_event_digest():
    source = _corpus()

    digests = {
        corpus_event_digest(prepare_profile_corpus(source, profile))
        for profile in ("conversation", "atomic", "timeline", "reference")
    }

    assert digests == {corpus_event_digest(source)}


def test_corpus_sample_is_deterministic_and_spans_the_time_range():
    source = _corpus()

    sampled = sample_corpus(source, documents=2)

    assert [document.document_id for document in sampled.documents] == [
        source.documents[0].document_id,
        source.documents[-1].document_id,
    ]


def test_profile_summary_keeps_recall_and_latency_separate():
    result = ProfileBenchmarkResult(
        profiles=("conversation", "reference", "timeline"),
        cases=(
            {
                "case": {"case_id": "a"},
                "measurements": {
                    "conversation": {"elapsed_ms": 100.0, "raw_context": "a"},
                    "timeline": {"elapsed_ms": 50.0, "raw_context": "b"},
                    "reference": {"elapsed_ms": 25.0, "raw_context": "c"},
                },
                "grades": {
                    "conversation": {
                        "answer_coverage": 4,
                        "attribution": 4,
                        "temporal": None,
                        "contradiction": False,
                    },
                    "timeline": {
                        "answer_coverage": 3,
                        "attribution": 4,
                        "temporal": None,
                        "contradiction": False,
                    },
                    "reference": {
                        "answer_coverage": 1,
                        "attribution": 2,
                        "temporal": None,
                        "contradiction": True,
                    },
                },
            },
        ),
    )

    summary = summarize_profile_benchmark(result)

    assert summary["conversation"]["mean_coverage"] == 4.0
    assert summary["timeline"]["success_rate"] == 1.0
    assert summary["reference"]["contradiction_rate"] == 1.0
    assert summary["reference"]["latency_p50_ms"] == 25.0


def test_profile_comparison_reports_paired_differences_and_wins():
    result = ProfileBenchmarkResult(
        profiles=("left", "right"),
        cases=(
            {
                "grades": {
                    "left": {"answer_coverage": 4},
                    "right": {"answer_coverage": 2},
                }
            },
            {
                "grades": {
                    "left": {"answer_coverage": 1},
                    "right": {"answer_coverage": 3},
                }
            },
            {
                "grades": {
                    "left": {"answer_coverage": 4},
                    "right": {"answer_coverage": 4},
                }
            },
        ),
    )

    comparison = compare_profiles(result, "left", "right", bootstrap_samples=100)

    assert comparison["left_wins"] == 1
    assert comparison["right_wins"] == 1
    assert comparison["ties"] == 1
    assert comparison["coverage_difference"] == 0.0
    assert comparison["success_difference"] == 0.0


def test_agent_context_matches_production_item_and_character_limits():
    records = tuple(
        MemoryRecord(
            backend="test",
            memory_id=f"memory-{index}",
            text=f"fact-{index}",
            memory_type="world",
        )
        for index in range(60)
    )

    context = render_agent_context(records)

    assert len(context) <= 4_000
    assert "memory_id: memory-49" in context
    assert "memory_id: memory-50" not in context


@pytest.mark.asyncio
async def test_disabled_observations_do_not_wait_on_hindsight_consolidation(
    monkeypatch,
):
    class Response:
        status = 200

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def text(self):
            return json.dumps(
                {
                    "pending_operations": 0,
                    "pending_consolidation": 379,
                    "failed_operations": 0,
                    "failed_consolidation": 0,
                }
            )

    class Session:
        calls = 0

        def request(self, *_args, **_kwargs):
            self.calls += 1
            return Response()

    async def no_sleep(_seconds):
        return None

    monkeypatch.setattr(backends.asyncio, "sleep", no_sleep)
    session = Session()

    result = await wait_for_hindsight_idle(
        session,
        "http://memory",
        "bank",
        wait_for_consolidation=False,
    )

    assert result["pending_consolidation"] == 379
    assert session.calls == 3
