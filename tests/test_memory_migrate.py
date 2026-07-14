from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from aiohttp import web
import pytest

from telefire.memory_migrate import inventory_legacy_store, migrate_legacy_store

zvec = pytest.importorskip("zvec")


def memory_schema(dimension):
    return zvec.CollectionSchema(
        name="telefire_memory",
        fields=[
            zvec.FieldSchema("record_type", zvec.DataType.STRING),
            zvec.FieldSchema("subject_id", zvec.DataType.STRING),
            zvec.FieldSchema("scope_id", zvec.DataType.STRING),
            zvec.FieldSchema("text", zvec.DataType.STRING),
            zvec.FieldSchema("occurred_at_ms", zvec.DataType.INT64),
            zvec.FieldSchema("created_at_ms", zvec.DataType.INT64),
            zvec.FieldSchema("fingerprint", zvec.DataType.STRING),
            zvec.FieldSchema("source_observation_id", zvec.DataType.STRING),
            zvec.FieldSchema("metadata_json", zvec.DataType.STRING),
            zvec.FieldSchema("suppressed", zvec.DataType.BOOL),
        ],
        vectors=zvec.VectorSchema(
            "embedding",
            zvec.DataType.VECTOR_FP32,
            dimension=dimension,
        ),
    )


def record_doc(**values):
    vector = values.pop("vector")
    record_id = values.pop("record_id")
    suppressed = values.pop("suppressed", False)
    return zvec.Doc(
        id=record_id,
        fields={**values, "suppressed": suppressed},
        vectors={"embedding": vector},
    )


def legacy_store(
    path: Path,
    *,
    include_suppressed: bool = False,
    include_malformed: bool = False,
) -> Path:
    path.mkdir()
    (path / "manifest.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "embedding_model": "legacy-model",
                "embedding_dimension": 4,
            }
        )
    )
    (path / "identities.json").write_text(
        json.dumps(
            {
                "telegram:user:20": "Alice Example",
                "telegram:chat:-1001": "Engineering Group",
            }
        )
    )
    collection = zvec.create_and_open(
        str(path / "records"),
        schema=memory_schema(4),
    )
    vector = [0.1, 0.2, 0.3, 0.4]
    docs = [
        record_doc(
            record_id="observation-1",
            record_type="observation",
            subject_id="telegram:user:20",
            scope_id="telegram:chat:-1001",
            text="Alice asked about vector databases.",
            occurred_at_ms=1_784_000_000_000,
            created_at_ms=1_784_000_001_000,
            fingerprint="fingerprint-1",
            source_observation_id="",
            metadata_json=json.dumps({"client": "telegram", "count": 2}),
            vector=vector,
        ),
        record_doc(
            record_id="fact-1",
            record_type="fact",
            subject_id="telegram:user:20",
            scope_id="telegram:chat:-1001",
            text="Alice likes vectors.",
            occurred_at_ms=1_784_000_000_000,
            created_at_ms=1_784_000_001_000,
            fingerprint="",
            source_observation_id="observation-1",
            metadata_json="{}",
            vector=vector,
        ),
        record_doc(
            record_id="profile-1",
            record_type="profile",
            subject_id="telegram:user:20",
            scope_id="",
            text="# User Profile\n\n- Derived profile",
            occurred_at_ms=1_784_000_000_000,
            created_at_ms=1_784_000_001_000,
            fingerprint="",
            source_observation_id="",
            metadata_json="{}",
            vector=vector,
        ),
    ]
    if include_suppressed:
        docs.append(
            record_doc(
                record_id="suppressed-fact-1",
                record_type="fact",
                subject_id="telegram:user:20",
                scope_id="telegram:chat:-1001",
                text="Alice asked about vector databases.",
                occurred_at_ms=1_784_000_000_000,
                created_at_ms=1_784_000_001_000,
                fingerprint="",
                source_observation_id="observation-1",
                metadata_json="{}",
                suppressed=True,
                vector=vector,
            )
        )
    if include_malformed:
        docs.append(
            record_doc(
                record_id="malformed-observation-1",
                record_type="observation",
                subject_id="telegram:user:20",
                scope_id="telegram:chat:-1001",
                text="",
                occurred_at_ms=1_784_000_000_000,
                created_at_ms=1_784_000_001_000,
                fingerprint="",
                source_observation_id="",
                metadata_json="{}",
                vector=vector,
            )
        )
    statuses = collection.insert(docs)
    assert all(status.ok() for status in statuses)
    return path


async def start_hindsight() -> tuple[web.AppRunner, str, dict[str, Any]]:
    state: dict[str, Any] = {
        "documents": {},
        "invalidated": False,
        "patches": [],
        "queued_reflection_ids": [],
        "profiles": [],
        "reflects": [],
        "retains": [],
    }
    app = web.Application()

    async def upsert_bank(request):
        payload = await request.json()
        state["profiles"].append(payload)
        return web.json_response(
            {
                "bank_id": request.match_info["bank_id"],
                "name": payload["name"],
                "disposition": {"empathy": 3, "literalism": 3, "skepticism": 3},
                "mission": "",
            }
        )

    async def retain(request):
        payload = await request.json()
        state["retains"].append(payload)
        for item in payload["items"]:
            state["documents"][item["document_id"]] = item["content"]
        return web.json_response(
            {
                "success": True,
                "bank_id": request.match_info["bank_id"],
                "items_count": len(payload["items"]),
                "async": False,
            }
        )

    async def get_document(request):
        document_id = request.match_info["document_id"]
        content = state["documents"].get(document_id)
        if content is None:
            raise web.HTTPNotFound
        return web.json_response({"id": document_id, "original_text": content})

    async def reflect(request):
        state["reflects"].append(await request.json())
        if state["queued_reflection_ids"]:
            memory_ids = state["queued_reflection_ids"].pop(0)
        else:
            memory_ids = [] if state["invalidated"] else ["memory-vector"]
        return web.json_response(
            {
                "structured_output": {"invalidate_memory_ids": memory_ids},
                "based_on": {
                    "memories": [
                        {"id": memory_id, "text": "Alice asked about vectors."}
                        for memory_id in memory_ids
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
                "state": "active",
            }
        )

    async def patch_memory(request):
        payload = await request.json()
        state["patches"].append(payload)
        state["invalidated"] = True
        return web.json_response(
            {"id": request.match_info["memory_id"], "state": "invalidated"}
        )

    app.router.add_put("/v1/default/banks/{bank_id}", upsert_bank)
    app.router.add_post("/v1/default/banks/{bank_id}/memories", retain)
    app.router.add_get(
        "/v1/default/banks/{bank_id}/documents/{document_id}", get_document
    )
    app.router.add_post("/v1/default/banks/{bank_id}/reflect", reflect)
    app.router.add_get("/v1/default/banks/{bank_id}/memories/{memory_id}", get_memory)
    app.router.add_patch(
        "/v1/default/banks/{bank_id}/memories/{memory_id}", patch_memory
    )
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    return runner, f"http://127.0.0.1:{port}", state


def test_migration_dry_run_inventories_only_source_observations(tmp_path):
    source = legacy_store(tmp_path / "legacy")
    inventory = inventory_legacy_store(source)

    assert len(inventory.observations) == 1
    assert inventory.observations[0].text == "Alice asked about vector databases."
    assert inventory.suppressions == ()
    assert inventory.identities["telegram:user:20"] == "Alice Example"
    assert inventory.report.examined == 3
    assert inventory.report.source_documents == 3
    assert inventory.report.source_embedding_model == "legacy-model"
    assert inventory.report.source_embedding_dimension == 4
    assert inventory.report.skipped == {
        "derived_memory": 1,
        "unrecoverable_profile_only_state": 1,
    }


def test_migration_dry_run_counts_malformed_source_records(tmp_path):
    source = legacy_store(tmp_path / "legacy", include_malformed=True)

    inventory = inventory_legacy_store(source)

    assert inventory.report.examined == 4
    assert len(inventory.observations) == 1
    assert inventory.report.skipped["malformed_observation"] == 1


def test_migration_inventories_recoverable_suppressed_derived_records(tmp_path):
    source = legacy_store(tmp_path / "legacy", include_suppressed=True)

    inventory = inventory_legacy_store(source)

    assert len(inventory.suppressions) == 1
    suppression = inventory.suppressions[0]
    assert suppression.record_id == "suppressed-fact-1"
    assert suppression.record_type == "fact"
    assert suppression.source_observation_id == "observation-1"
    assert inventory.report.recoverable_suppressions == 1
    assert inventory.report.examined == 4
    assert inventory.report.skipped == {
        "derived_memory": 1,
        "unrecoverable_profile_only_state": 1,
    }


def test_migration_rejects_a_legacy_store_over_100000_documents(tmp_path, monkeypatch):
    source = tmp_path / "legacy"
    source.mkdir()
    (source / "manifest.json").write_text(json.dumps({"schema_version": 1}))
    (source / "records").mkdir()

    class TooLargeCollection:
        stats = SimpleNamespace(doc_count=100_001)

        def query(self, **kwargs):
            raise AssertionError("oversized stores must fail before querying Zvec")

    monkeypatch.setattr(zvec, "open", lambda path: TooLargeCollection())

    with pytest.raises(ValueError, match="100000"):
        inventory_legacy_store(source)


@pytest.mark.asyncio
async def test_migration_executes_idempotently_with_labels_and_no_vectors(tmp_path):
    source = legacy_store(tmp_path / "legacy")
    runner, url, state = await start_hindsight()
    state_path = tmp_path / "ai.db"
    try:
        first = await migrate_legacy_store(
            source=source,
            hindsight_url=url,
            state_path=state_path,
            execute=True,
        )
        second = await migrate_legacy_store(
            source=source,
            hindsight_url=url,
            state_path=state_path,
            execute=True,
        )

        assert first.accepted == 1
        assert first.unchanged == 0
        assert first.failed == 0
        assert second.accepted == 0
        assert second.unchanged == 1
        assert len(state["retains"]) == 1
        item = state["retains"][0]["items"][0]
        assert item["document_id"] == "legacy:zvec:observation-1"
        assert "embedding" not in json.dumps(item)
        content = json.loads(item["content"])
        event = content["events"][0]
        assert event["actor"] == {
            "id": "telegram:user:20",
            "display_name": "Alice Example",
        }
        assert event["metadata"]["migration"] == "zvec-v1"
        assert first.labels == 2
    finally:
        await runner.cleanup()


@pytest.mark.asyncio
@pytest.mark.parametrize("destination_state", ["missing", "different"])
async def test_migration_resubmits_when_receipt_matches_but_destination_is_not_exact(
    tmp_path, destination_state
):
    source = legacy_store(tmp_path / "legacy")
    runner, url, state = await start_hindsight()
    state_path = tmp_path / "ai.db"
    document_id = "legacy:zvec:observation-1"
    try:
        first = await migrate_legacy_store(
            source=source,
            hindsight_url=url,
            state_path=state_path,
            execute=True,
        )
        if destination_state == "missing":
            del state["documents"][document_id]
        else:
            state["documents"][document_id] = "different destination content"

        retry = await migrate_legacy_store(
            source=source,
            hindsight_url=url,
            state_path=state_path,
            execute=True,
        )

        assert first.accepted == 1
        assert retry.accepted == 1
        assert retry.unchanged == 0
        retained_ids = [
            item["document_id"]
            for payload in state["retains"]
            for item in payload["items"]
        ]
        assert retained_ids == [document_id, document_id]
    finally:
        await runner.cleanup()


@pytest.mark.asyncio
async def test_migration_preserves_suppression_with_stable_idempotent_correction(
    tmp_path,
):
    source = legacy_store(tmp_path / "legacy", include_suppressed=True)
    runner, url, state = await start_hindsight()
    state_path = tmp_path / "ai.db"
    try:
        first = await migrate_legacy_store(
            source=source,
            hindsight_url=url,
            state_path=state_path,
            execute=True,
        )
        second = await migrate_legacy_store(
            source=source,
            hindsight_url=url,
            state_path=state_path,
            execute=True,
        )

        assert first.accepted == 2
        assert first.unchanged == 0
        assert first.invalidated == 1
        assert first.failed == 0
        assert second.accepted == 0
        assert second.unchanged == 2
        assert second.invalidated == 0
        assert second.failed == 0
        assert len(state["reflects"]) == 1
        assert len(state["patches"]) == 1

        retained = [item for payload in state["retains"] for item in payload["items"]]
        assert len(retained) == 2
        correction = next(
            item
            for item in retained
            if item["document_id"] == "legacy:zvec:suppression:suppressed-fact-1"
        )
        correction_content = json.loads(correction["content"])
        correction_event = correction_content["events"][0]
        assert correction_event["actor"]["id"] == "telefire:legacy-migration"
        assert correction_event["metadata"] == {
            "legacy_record_id": "suppressed-fact-1",
            "legacy_record_type": "fact",
            "legacy_source_observation_id": "observation-1",
            "migration": "zvec-v1-suppression",
        }
        assert "Alice asked about vector databases." in state["reflects"][0]["query"]
    finally:
        await runner.cleanup()


@pytest.mark.asyncio
async def test_suppression_without_active_candidate_converges_on_correction_evidence(
    tmp_path,
):
    source = legacy_store(tmp_path / "legacy", include_suppressed=True)
    runner, url, state = await start_hindsight()
    state["queued_reflection_ids"] = [[]]
    state_path = tmp_path / "ai.db"
    try:
        first = await migrate_legacy_store(
            source=source,
            hindsight_url=url,
            state_path=state_path,
            execute=True,
        )
        second = await migrate_legacy_store(
            source=source,
            hindsight_url=url,
            state_path=state_path,
            execute=True,
        )
        assert first.accepted == 2
        assert first.invalidated == 0
        assert first.failed == 0
        assert second.unchanged == 2
        assert second.invalidated == 0
        assert second.failed == 0
        assert len(state["reflects"]) == 1
        retained = [item for payload in state["retains"] for item in payload["items"]]
        assert len(retained) == 2
    finally:
        await runner.cleanup()
