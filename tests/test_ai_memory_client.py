import asyncio
from dataclasses import replace
import json
from datetime import UTC, datetime

from aiohttp import web
import pytest

from telefire.ai_memory import (
    HindsightMemoryClient,
    MemoryClientError,
    MemoryDocumentReceipt,
    MemoryEpisode,
    MemoryEvent,
    MemoryRetainResult,
    MemoryRevisionResult,
    append_episode_once,
    retain_episode_once,
)


def episode() -> MemoryEpisode:
    return MemoryEpisode(
        scope_id="telegram:chat:-1001",
        scope_display_name="Engineering Group",
        document_id="telegram:thread:-1001:41",
        events=(
            MemoryEvent(
                source_id="telegram:message:-1001:41",
                actor_id="telegram:user:10",
                actor_display_name="Alice Example",
                occurred_at=datetime(2026, 7, 11, 8, 0, tzinfo=UTC),
                text="I use PostgreSQL at work.",
            ),
            MemoryEvent(
                source_id="telegram:message:-1001:42",
                actor_id="telegram:user:20",
                actor_display_name="Bob Example",
                occurred_at=datetime(2026, 7, 11, 8, 5, tzinfo=UTC),
                mentioned_at=datetime(2026, 7, 12, 9, 5, tzinfo=UTC),
                text="I will finish the migration today.",
                reply_to_source_id="telegram:message:-1001:41",
                mentioned_actors=(("telegram:user:10", "Alice Example"),),
                metadata={
                    "quotation": {
                        "source_id": "telegram:message:-1001:41",
                        "text": "I use PostgreSQL at work.",
                    }
                },
            ),
        ),
    )


def test_fact_entity_hints_are_conditional_for_multiple_events():
    item = episode()

    assert item.actor_ids == ("telegram:user:10", "telegram:user:20")
    assert item.fact_entity_hints == (
        ("telegram:user:10", ("Alice Example",)),
        ("telegram:user:20", ("Bob Example",)),
    )

    single_event = replace(item, events=(item.events[1],))
    assert single_event.fact_entity_hints == (
        ("telegram:user:20", None),
        ("telegram:user:10", None),
    )


def test_fact_entity_hints_omit_ambiguous_display_names():
    item = episode()
    ambiguous = replace(
        item,
        events=(
            replace(item.events[0], actor_display_name="Alex"),
            replace(
                item.events[1],
                actor_display_name="Alex",
                mentioned_actors=(),
            ),
        ),
    )

    assert ambiguous.fact_entity_hints == ()


async def start_server(configure):
    app = web.Application()
    configure(app)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    return runner, f"http://127.0.0.1:{port}"


@pytest.mark.asyncio
async def test_hindsight_client_retains_episode_and_renders_bank_recall():
    received = {"bank_profiles": []}

    async def upsert_bank(request):
        payload = await request.json()
        received["bank_profiles"].append(
            (request.match_info["bank_id"], payload)
        )
        return web.json_response(
            {
                "bank_id": request.match_info["bank_id"],
                "name": payload["name"],
                "disposition": {"empathy": 3, "literalism": 3, "skepticism": 3},
                "mission": "",
            }
        )

    async def retain(request):
        received["retain"] = await request.json()
        received["retain_bank"] = request.match_info["bank_id"]
        return web.json_response(
            {
                "success": True,
                "bank_id": "telegram:chat:-1001",
                "items_count": len(received["retain"]["items"]),
                "async": False,
            }
        )

    async def recall(request):
        received["recall"] = await request.json()
        received["recall_bank"] = request.match_info["bank_id"]
        return web.json_response(
            {
                "results": [
                    {
                        "id": "memory-1",
                        "text": "Bob will finish the migration today.",
                        "type": "world",
                        "entities": ["telegram:user:20"],
                        "occurred_start": "2026-07-11T08:05:00Z",
                        "occurred_end": "2026-07-11T08:05:00Z",
                        "mentioned_at": "2026-07-11T08:05:00Z",
                        "document_id": "telegram:thread:-1001:41",
                        "chunk_id": "chunk-1",
                    }
                ]
            }
        )

    def configure(app):
        app.router.add_put("/v1/default/banks/{bank_id}", upsert_bank)
        app.router.add_post("/v1/default/banks/{bank_id}/memories", retain)
        app.router.add_post("/v1/default/banks/{bank_id}/memories/recall", recall)

    runner, url = await start_server(configure)
    client = HindsightMemoryClient(url)
    item = episode()
    try:
        retained = await client.retain(item)
        recalled = await client.recall(
            scope_id=item.scope_id,
            query="Who owns the migration?",
        )

        assert retained.accepted is True
        assert received["bank_profiles"] == [
            (item.scope_id, {"name": "Engineering Group"})
        ]
        assert received["retain_bank"] == item.scope_id
        request_item = received["retain"]["items"][0]
        assert request_item["document_id"] == item.document_id
        assert request_item["update_mode"] == "replace"
        assert request_item["metadata"]["content_hash"] == item.content_hash
        assert request_item["entities"] == [
            {
                "text": "telegram:user:10",
                "type": "PERSON",
                "match_aliases": ["Alice Example"],
            },
            {
                "text": "telegram:user:20",
                "type": "PERSON",
                "match_aliases": ["Bob Example"],
            },
        ]
        retained_content = json.loads(request_item["content"])
        assert retained_content["schema"] == "telefire.memory.episode.v1"
        assert retained_content["events"][1]["actor"] == {
            "id": "telegram:user:20",
            "display_name": "Bob Example",
        }
        assert retained_content["events"][1]["reply_to_source_id"] == (
            "telegram:message:-1001:41"
        )
        assert retained_content["events"][1]["mentioned_at"] == ("2026-07-12T09:05:00Z")
        assert retained_content["events"][1]["metadata"]["quotation"] == {
            "source_id": "telegram:message:-1001:41",
            "text": "I use PostgreSQL at work.",
        }
        assert retained_content["events"][1]["mentioned_actors"] == [
            {"id": "telegram:user:10", "display_name": "Alice Example"}
        ]
        assert received["recall_bank"] == item.scope_id
        assert received["recall"]["query"] == "Who owns the migration?"
        rendered = recalled.render()
        assert "Bob will finish the migration today" in rendered
        assert "telegram:user:20" in rendered
        assert "telegram:thread:-1001:41#chunk-1" in rendered

        second = replace(item, document_id="telegram:thread:-1001:99")
        batch = await client.retain_many((item, second))
        assert batch.items_count == 2
        assert len(received["bank_profiles"]) == 1
        assert [entry["document_id"] for entry in received["retain"]["items"]] == [
            item.document_id,
            second.document_id,
        ]
    finally:
        await client.close()
        await runner.cleanup()


@pytest.mark.asyncio
async def test_retry_after_receipt_crash_replaces_one_complete_episode():
    class ReplaceMemory:
        def __init__(self):
            self.documents = {}
            self.modes = []

        async def retain_many(self, episodes, *, update_mode="replace"):
            self.modes.append(update_mode)
            for item in episodes:
                assert update_mode == "replace"
                self.documents[(item.scope_id, item.document_id)] = item.content
            return MemoryRetainResult(accepted=True, items_count=len(episodes))

    class FlakyReceipts:
        def __init__(self):
            self.saved = None
            self.fail_once = True

        async def get_memory_document_receipt(self, scope_id, document_id):
            return self.saved

        async def save_memory_document_receipt(
            self,
            scope_id,
            document_id,
            content_hash,
            event_versions,
        ):
            if self.fail_once:
                self.fail_once = False
                raise OSError("simulated receipt fsync failure")
            self.saved = MemoryDocumentReceipt(content_hash, event_versions)

    item = episode()
    memory = ReplaceMemory()
    receipts = FlakyReceipts()

    with pytest.raises(OSError, match="fsync"):
        await retain_episode_once(memory, receipts, item)
    assert json.loads(memory.documents[(item.scope_id, item.document_id)])["events"]

    assert await retain_episode_once(memory, receipts, item) is True
    assert memory.modes == ["replace", "replace"]
    assert memory.documents[(item.scope_id, item.document_id)] == item.content


@pytest.mark.asyncio
async def test_append_episode_merges_receipt_versions_and_is_idempotent():
    class AppendMemory:
        def __init__(self):
            self.calls = []

        async def retain_many(self, episodes, *, update_mode="replace"):
            self.calls.append((episodes, update_mode))
            return MemoryRetainResult(accepted=True, items_count=len(episodes))

    class Receipts:
        def __init__(self, saved):
            self.saved = saved

        async def get_memory_document_receipt(self, scope_id, document_id):
            return self.saved

        async def save_memory_document_receipt(
            self,
            scope_id,
            document_id,
            content_hash,
            event_versions,
        ):
            self.saved = MemoryDocumentReceipt(content_hash, event_versions)

    complete = replace(
        episode(),
        document_id="telegram:dream-segment:-1001:40-59",
    )
    first_only = replace(complete, events=complete.events[:1])
    receipts = Receipts(
        MemoryDocumentReceipt(
            first_only.content_hash,
            first_only.event_versions,
        )
    )
    memory = AppendMemory()

    assert await append_episode_once(memory, receipts, complete) is True
    assert memory.calls[0][1] == "append"
    assert receipts.saved.event_versions == complete.event_versions

    assert await append_episode_once(memory, receipts, complete) is False
    assert len(memory.calls) == 1


@pytest.mark.asyncio
async def test_hindsight_revision_invalidates_only_cited_target_memory():
    received = {"patch": []}

    async def reflect(request):
        received["reflect"] = await request.json()
        return web.json_response(
            {
                "text": "The earlier tea preference conflicts.",
                "structured_output": {
                    "invalidate_memory_ids": ["memory-tea", "memory-other"]
                },
                "based_on": {
                    "memories": [
                        {"id": "memory-tea", "text": "Prefers tea", "type": "world"},
                        {"id": "memory-other", "text": "Likes tea", "type": "world"},
                    ]
                },
            }
        )

    async def get_memory(request):
        memory_id = request.match_info["memory_id"]
        return web.json_response(
            {
                "id": memory_id,
                "type": "world",
                "entities": (
                    ["telegram:user:20"]
                    if memory_id == "memory-tea"
                    else ["telegram:user:30"]
                ),
            }
        )

    async def patch_memory(request):
        received["patch"].append(
            (request.match_info["memory_id"], await request.json())
        )
        return web.json_response(
            {"id": request.match_info["memory_id"], "state": "invalidated"}
        )

    def configure(app):
        app.router.add_post("/v1/default/banks/{bank_id}/reflect", reflect)
        app.router.add_get(
            "/v1/default/banks/{bank_id}/memories/{memory_id}", get_memory
        )
        app.router.add_patch(
            "/v1/default/banks/{bank_id}/memories/{memory_id}", patch_memory
        )

    runner, url = await start_server(configure)
    client = HindsightMemoryClient(url)
    try:
        result = await client.revise(
            scope_id="telegram:chat:-1001",
            subject_id="telegram:user:20",
            instruction="Correct the preference to coffee",
        )

        assert result == MemoryRevisionResult(invalidated_count=1)
        assert received["reflect"]["budget"] == "mid"
        assert received["reflect"]["fact_types"] == ["world", "experience"]
        assert "The user now prefers coffee" not in received["reflect"]["query"]
        assert received["patch"] == [
            (
                "memory-tea",
                {
                    "state": "invalidated",
                    "reason": "Owner revision: Correct the preference to coffee",
                },
            )
        ]
    finally:
        await client.close()
        await runner.cleanup()


@pytest.mark.asyncio
async def test_hindsight_revision_retries_uncited_selection_once():
    received = {"reflect_calls": 0, "patch": []}

    async def reflect(request):
        await request.json()
        received["reflect_calls"] += 1
        if received["reflect_calls"] == 1:
            return web.json_response(
                {
                    "text": "invalid",
                    "structured_output": {
                        "invalidate_memory_ids": ["invented-id"]
                    },
                    "based_on": {"memories": []},
                }
            )
        return web.json_response(
            {
                "text": "The earlier tea preference conflicts.",
                "structured_output": {
                    "invalidate_memory_ids": ["memory-tea"]
                },
                "based_on": {
                    "memories": [
                        {
                            "id": "memory-tea",
                            "text": "Prefers tea",
                            "type": "world",
                        }
                    ]
                },
            }
        )

    async def get_memory(request):
        return web.json_response(
            {
                "id": request.match_info["memory_id"],
                "type": "world",
                "entities": ["telegram:user:20"],
            }
        )

    async def patch_memory(request):
        received["patch"].append(
            (request.match_info["memory_id"], await request.json())
        )
        return web.json_response(
            {"id": request.match_info["memory_id"], "state": "invalidated"}
        )

    def configure(app):
        app.router.add_post("/v1/default/banks/{bank_id}/reflect", reflect)
        app.router.add_get(
            "/v1/default/banks/{bank_id}/memories/{memory_id}", get_memory
        )
        app.router.add_patch(
            "/v1/default/banks/{bank_id}/memories/{memory_id}", patch_memory
        )

    runner, url = await start_server(configure)
    client = HindsightMemoryClient(url)
    try:
        result = await client.revise(
            scope_id="telegram:chat:-1001",
            subject_id="telegram:user:20",
            instruction="Correct the preference to coffee",
        )

        assert result == MemoryRevisionResult(invalidated_count=1)
        assert received["reflect_calls"] == 2
        assert received["patch"] == [
            (
                "memory-tea",
                {
                    "state": "invalidated",
                    "reason": "Owner revision: Correct the preference to coffee",
                },
            )
        ]
    finally:
        await client.close()
        await runner.cleanup()


@pytest.mark.asyncio
async def test_hindsight_revision_rejects_uncited_memory_selection():
    reflect_calls = 0

    async def reflect(request):
        nonlocal reflect_calls
        await request.json()
        reflect_calls += 1
        return web.json_response(
            {
                "text": "invalid",
                "structured_output": {"invalidate_memory_ids": ["invented-id"]},
                "based_on": {"memories": []},
            }
        )

    def configure(app):
        app.router.add_post("/v1/default/banks/{bank_id}/reflect", reflect)

    runner, url = await start_server(configure)
    client = HindsightMemoryClient(url)
    try:
        with pytest.raises(MemoryClientError, match="uncited"):
            await client.revise(
                scope_id="telegram:chat:-1001",
                subject_id="telegram:user:20",
                instruction="Forget tea",
            )
        assert reflect_calls == 2
    finally:
        await client.close()
        await runner.cleanup()


@pytest.mark.asyncio
async def test_hindsight_client_rejects_malformed_recall():
    async def malformed(request):
        await request.json()
        return web.json_response({"results": [{"id": "missing-text"}]})

    def configure(app):
        app.router.add_post("/v1/default/banks/{bank_id}/memories/recall", malformed)

    runner, url = await start_server(configure)
    client = HindsightMemoryClient(url)
    try:
        with pytest.raises(MemoryClientError, match="malformed"):
            await client.recall(
                scope_id="telegram:chat:-1001",
                query="profile",
            )
    finally:
        await client.close()
        await runner.cleanup()


@pytest.mark.asyncio
async def test_hindsight_client_enforces_timeout():
    async def slow(request):
        await request.json()
        await asyncio.sleep(0.1)
        return web.json_response({"results": []})

    def configure(app):
        app.router.add_post("/v1/default/banks/{bank_id}/memories/recall", slow)

    runner, url = await start_server(configure)
    client = HindsightMemoryClient(url, timeout=0.01)
    try:
        with pytest.raises(TimeoutError):
            await client.recall(
                scope_id="telegram:chat:-1001",
                query="profile",
            )
    finally:
        await client.close()
        await runner.cleanup()
