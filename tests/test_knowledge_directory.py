from __future__ import annotations

from datetime import UTC, datetime

from aiohttp import web
import pytest

from telefire.ai_memory import HindsightMemoryClient, MemoryClientError
from telefire.memory_directory import (
    KNOWLEDGE_DIRECTORY_BANK_ID,
    DirectoryPublication,
    DirectorySource,
    bank_reference_tag,
)


async def _start_server(configure):
    app = web.Application()
    configure(app)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    return runner, f"http://127.0.0.1:{port}"


def _publication() -> DirectoryPublication:
    return DirectoryPublication(
        publication_id="telegram:message:-1007:91",
        publisher_id="telegram:user:42",
        published_at=datetime(2026, 7, 16, 12, 30, tzinfo=UTC),
        source=DirectorySource(
            bank_id="telegram:chat:-100123",
            display_name="Coder Offtopic",
            platform="telegram",
            source_kind="group",
            attributes={"username": "CoderOfftopic", "title": "Coder Offtopic"},
        ),
        description="中文技术群，也常被称为 Coder OT 群。",
    )


@pytest.mark.asyncio
async def test_directory_publication_uses_one_trusted_reference_scope():
    received: dict[str, object] = {"profiles": []}

    async def upsert_bank(request):
        payload = await request.json()
        received["profiles"].append((request.match_info["bank_id"], payload))
        return web.json_response(
            {"bank_id": request.match_info["bank_id"], "name": payload["name"]}
        )

    async def retain(request):
        received["retain_bank"] = request.match_info["bank_id"]
        received["retain"] = await request.json()
        return web.json_response(
            {
                "success": True,
                "bank_id": request.match_info["bank_id"],
                "items_count": 1,
                "async": False,
            }
        )

    def configure(app):
        app.router.add_put("/v1/default/banks/{bank_id}", upsert_bank)
        app.router.add_post("/v1/default/banks/{bank_id}/memories", retain)

    runner, url = await _start_server(configure)
    client = HindsightMemoryClient(url)
    publication = _publication()
    tag = bank_reference_tag(publication.source.bank_id)
    try:
        result = await client.publish_directory(publication)

        assert result.accepted is True
        assert received["profiles"] == [
            (KNOWLEDGE_DIRECTORY_BANK_ID, {"name": "Knowledge Directory"}),
            (publication.source.bank_id, {"name": publication.source.display_name}),
        ]
        assert received["retain_bank"] == KNOWLEDGE_DIRECTORY_BANK_ID
        payload = received["retain"]
        assert isinstance(payload, dict)
        item = payload["items"][0]
        assert item["tags"] == [tag]
        assert item["observation_scopes"] == [[tag]]
        assert item["update_mode"] == "replace"
        assert item["metadata"] == {
            "client": "telefire",
            "source": "knowledge-directory",
            "schema": "telefire.knowledge-directory.v1",
            "bank_id": publication.source.bank_id,
            "bank_ref": tag,
            "source_name": publication.source.display_name,
            "source_platform": "telegram",
            "source_kind": "group",
        }
        assert publication.source.bank_id not in item["content"]
        assert "Coder OT" in item["content"]
    finally:
        await client.close()
        await runner.cleanup()


@pytest.mark.asyncio
async def test_directory_recall_uses_exact_or_filters_and_validates_references():
    received: dict[str, object] = {}
    allowed = ("telegram:chat:-100123", "qq:group:686743769")
    tags = {bank_id: bank_reference_tag(bank_id) for bank_id in allowed}

    async def recall(request):
        received["bank_id"] = request.match_info["bank_id"]
        received["body"] = await request.json()
        return web.json_response(
            {
                "results": [
                    {
                        "id": "memory-1",
                        "text": "Coder OT 群是一个中文技术群。",
                        "type": "world",
                        "entities": ["Coder Offtopic"],
                        "document_id": "directory-publication:one",
                        "tags": [tags[allowed[0]]],
                        "metadata": {
                            "client": "telefire",
                            "source": "knowledge-directory",
                            "schema": "telefire.knowledge-directory.v1",
                            "bank_id": allowed[0],
                            "bank_ref": tags[allowed[0]],
                            "source_name": "Coder Offtopic",
                            "source_platform": "telegram",
                            "source_kind": "group",
                        },
                    }
                ],
                "source_facts": {},
            }
        )

    def configure(app):
        app.router.add_post("/v1/default/banks/{bank_id}/memories/recall", recall)

    runner, url = await _start_server(configure)
    client = HindsightMemoryClient(url)
    try:
        result = await client.recall_directory(
            query="Coder OT 群最近在聊什么？",
            allowed_bank_ids=allowed,
        )

        assert received["bank_id"] == KNOWLEDGE_DIRECTORY_BANK_ID
        body = received["body"]
        assert isinstance(body, dict)
        assert body["tag_groups"] == [
            {
                "or": [
                    {"tags": [tags[allowed[0]]], "match": "exact"},
                    {"tags": [tags[allowed[1]]], "match": "exact"},
                ]
            }
        ]
        assert len(result.references) == 1
        reference = result.references[0]
        assert reference.bank_id == allowed[0]
        assert reference.display_name == "Coder Offtopic"
        assert reference.evidence[0].memory_id == "memory-1"
    finally:
        await client.close()
        await runner.cleanup()


@pytest.mark.asyncio
async def test_directory_recall_uses_source_fact_provenance_for_observations():
    bank_id = "telegram:chat:-100123"
    tag = bank_reference_tag(bank_id)
    source_fact = {
        "id": "source-fact-1",
        "text": "Coder Offtopic is a Chinese technology chat.",
        "type": "world",
        "tags": [tag],
        "metadata": {
            "client": "telefire",
            "source": "knowledge-directory",
            "schema": "telefire.knowledge-directory.v1",
            "bank_id": bank_id,
            "bank_ref": tag,
            "source_name": "Coder Offtopic",
            "source_platform": "telegram",
            "source_kind": "group",
        },
    }

    async def recall(_request):
        return web.json_response(
            {
                "results": [
                    {
                        "id": "observation-1",
                        "text": "Coder OT is a Chinese technology chat.",
                        "type": "observation",
                        "tags": [tag],
                        "metadata": {},
                        "source_fact_ids": ["source-fact-1"],
                    }
                ],
                "source_facts": {"source-fact-1": source_fact},
            }
        )

    def configure(app):
        app.router.add_post("/v1/default/banks/{bank_id}/memories/recall", recall)

    runner, url = await _start_server(configure)
    client = HindsightMemoryClient(url)
    try:
        result = await client.recall_directory(
            query="Coder OT",
            allowed_bank_ids=(bank_id,),
        )

        assert len(result.references) == 1
        assert result.references[0].bank_id == bank_id
        assert result.references[0].evidence[0].memory_id == "observation-1"
    finally:
        await client.close()
        await runner.cleanup()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "provenance",
    [
        {"metadata": {}, "source_fact_ids": ["source-fact-omitted-by-budget"]},
        {"metadata": None, "source_fact_ids": None},
    ],
)
async def test_directory_recall_skips_result_without_verifiable_provenance(
    provenance,
):
    bank_id = "telegram:chat:-100123"
    tag = bank_reference_tag(bank_id)

    async def recall(_request):
        item = {
            "id": "observation-1",
            "text": "Coder OT is a Chinese technology chat.",
            "type": "observation",
            "tags": [tag],
        }
        item.update(provenance)
        return web.json_response({"results": [item], "source_facts": {}})

    def configure(app):
        app.router.add_post("/v1/default/banks/{bank_id}/memories/recall", recall)

    runner, url = await _start_server(configure)
    client = HindsightMemoryClient(url)
    try:
        result = await client.recall_directory(
            query="Coder OT",
            allowed_bank_ids=(bank_id,),
        )

        assert result.references == ()
    finally:
        await client.close()
        await runner.cleanup()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mutation",
    [
        {"tags": []},
        {"tags": ["wrong-tag"]},
        {"metadata": {"schema": "telefire.knowledge-directory.v1"}},
        {
            "metadata": {
                "schema": "telefire.knowledge-directory.v1",
                "bank_id": "qq:group:999",
                "bank_ref": "wrong-tag",
                "source_name": "Wrong",
                "source_platform": "qq",
                "source_kind": "group",
            }
        },
    ],
)
async def test_directory_recall_rejects_untrusted_reference_shapes(mutation):
    bank_id = "telegram:chat:-100123"
    tag = bank_reference_tag(bank_id)
    item = {
        "id": "memory-1",
        "text": "Coder OT 群是一个中文技术群。",
        "type": "world",
        "entities": [],
        "document_id": "directory-publication:one",
        "tags": [tag],
        "metadata": {
            "client": "telefire",
            "source": "knowledge-directory",
            "schema": "telefire.knowledge-directory.v1",
            "bank_id": bank_id,
            "bank_ref": tag,
            "source_name": "Coder Offtopic",
            "source_platform": "telegram",
            "source_kind": "group",
        },
    }
    item.update(mutation)

    async def recall(_request):
        return web.json_response({"results": [item], "source_facts": {}})

    def configure(app):
        app.router.add_post("/v1/default/banks/{bank_id}/memories/recall", recall)

    runner, url = await _start_server(configure)
    client = HindsightMemoryClient(url)
    try:
        with pytest.raises(MemoryClientError, match="directory reference"):
            await client.recall_directory(
                query="Coder OT",
                allowed_bank_ids=(bank_id,),
            )
    finally:
        await client.close()
        await runner.cleanup()


@pytest.mark.asyncio
async def test_directory_publication_existence_requires_matching_document_contract():
    bank_id = "qq:group:686743769"
    tag = bank_reference_tag(bank_id)
    received: dict[str, object] = {}

    async def list_documents(request):
        received["query"] = request.query
        return web.json_response(
            {
                "items": [
                    {
                        "id": "directory-publication:incomplete",
                        "bank_id": KNOWLEDGE_DIRECTORY_BANK_ID,
                        "tags": [tag],
                    },
                    {
                        "id": "directory-publication:one",
                        "bank_id": KNOWLEDGE_DIRECTORY_BANK_ID,
                        "tags": [tag],
                    },
                ],
                "total": 2,
                "limit": 10,
                "offset": 0,
            }
        )

    async def get_document(request):
        received.setdefault("documents", []).append(request.match_info["document_id"])
        incomplete = request.match_info["document_id"].endswith(":incomplete")
        return web.json_response(
            {
                "id": request.match_info["document_id"],
                "bank_id": KNOWLEDGE_DIRECTORY_BANK_ID,
                "original_text": "source metadata",
                "content_hash": "hash",
                "created_at": "2026-07-16T12:30:00Z",
                "updated_at": "2026-07-16T12:30:00Z",
                "memory_unit_count": 1,
                "tags": [tag],
                "document_metadata": {
                    "client": "telefire",
                    "source": "knowledge-directory",
                    "schema": "telefire.knowledge-directory.v1",
                    "bank_id": bank_id,
                    "bank_ref": tag,
                    **(
                        {}
                        if incomplete
                        else {
                            "source_name": "Dog Food Filter",
                            "source_platform": "qq",
                            "source_kind": "group",
                        }
                    ),
                },
                "observation_scopes": [[tag]],
            }
        )

    def configure(app):
        app.router.add_get("/v1/default/banks/{bank_id}/documents", list_documents)
        app.router.add_get(
            "/v1/default/banks/{bank_id}/documents/{document_id}", get_document
        )

    runner, url = await _start_server(configure)
    client = HindsightMemoryClient(url)
    try:
        assert await client.is_directory_source_published(bank_id) is True
        assert received["query"].getall("tags") == [tag]
        assert received["query"]["tags_match"] == "all_strict"
        assert received["documents"] == [
            "directory-publication:incomplete",
            "directory-publication:one",
        ]
    finally:
        await client.close()
        await runner.cleanup()
