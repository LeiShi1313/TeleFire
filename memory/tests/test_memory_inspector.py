from __future__ import annotations

import aiohttp
from aiohttp import web
import pytest

from memory_inspector.app import InspectorSettings, create_app


async def start(app: web.Application) -> tuple[web.AppRunner, str]:
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    return runner, f"http://127.0.0.1:{port}"


async def hindsight_server() -> tuple[web.AppRunner, str]:
    app = web.Application()

    async def health(_):
        return web.json_response({"status": "healthy"})

    async def banks(_):
        return web.json_response(
            {
                "banks": [
                    {
                        "bank_id": "client:scope:one",
                        "name": "Engineering <script>alert(1)</script>",
                        "fact_count": 2,
                        "last_document_at": "2026-07-13T12:00:00Z",
                    }
                ]
            }
        )

    async def stats(request):
        return web.json_response(
            {
                "bank_id": request.match_info["bank_id"],
                "total_nodes": 4,
                "total_links": 3,
                "total_documents": 1,
                "nodes_by_fact_type": {"world": 1, "observation": 1},
                "links_by_link_type": {},
                "links_by_fact_type": {},
                "links_breakdown": {},
                "pending_operations": 0,
                "failed_operations": 0,
                "total_observations": 1,
            }
        )

    async def memories(_):
        return web.json_response(
            {
                "items": [
                    {
                        "id": "memory-1",
                        "type": "world",
                        "text": "<img src=x onerror=alert(1)> likes tea",
                        "entities": ["actor:20"],
                        "document_id": "conversation:41",
                        "state": "valid",
                    }
                ],
                "total": 1,
            }
        )

    async def memory(request):
        return web.json_response(
            {
                "id": request.match_info["memory_id"],
                "text": "Alice directly stated that the plan changed.",
                "type": "observation",
                "entities": ["actor:20"],
                "state": "valid",
                "occurred_start": "2026-07-13T12:00:00Z",
                "mentioned_at": "2026-07-13T12:01:00Z",
                "source_memories": [
                    {
                        "id": "source-memory-1",
                        "type": "world",
                        "text": "Alice said the plan changed.",
                    }
                ],
                "history": [
                    {
                        "state": "invalidated",
                        "reason": "Superseded by a direct correction",
                        "changed_at": "2026-07-13T12:02:00Z",
                    }
                ],
            }
        )

    async def documents(_):
        return web.json_response(
            {
                "items": [
                    {
                        "id": "conversation:41",
                        "memory_unit_count": 1,
                        "content_hash": "hash-1",
                    }
                ],
                "total": 1,
            }
        )

    async def entities(_):
        return web.json_response(
            {
                "items": [
                    {
                        "id": "entity-1",
                        "canonical_name": "actor:20",
                        "mention_count": 2,
                    }
                ],
                "total": 1,
            }
        )

    async def observations(_):
        return web.json_response({"scopes": [{"tags": [], "count": 1}]})

    async def document(request):
        return web.json_response(
            {
                "id": request.match_info["document_id"],
                "bank_id": request.match_info["bank_id"],
                "original_text": "Untrusted episode source",
            }
        )

    async def chunks(request):
        return web.json_response(
            {
                "items": [
                    {
                        "chunk_id": "chunk-1",
                        "document_id": request.match_info["document_id"],
                        "bank_id": request.match_info["bank_id"],
                        "chunk_index": 0,
                        "chunk_text": "Source chunk",
                    }
                ],
                "total": 1,
            }
        )

    app.router.add_get("/health", health)
    app.router.add_get("/v1/default/banks", banks)
    app.router.add_get("/v1/default/banks/{bank_id}/stats", stats)
    app.router.add_get("/v1/default/banks/{bank_id}/memories/list", memories)
    app.router.add_get("/v1/default/banks/{bank_id}/memories/{memory_id}", memory)
    app.router.add_get("/v1/default/banks/{bank_id}/documents", documents)
    app.router.add_get("/v1/default/banks/{bank_id}/entities", entities)
    app.router.add_get("/v1/default/banks/{bank_id}/observations/scopes", observations)
    app.router.add_get(
        "/v1/default/banks/{bank_id}/documents/{document_id}/chunks", chunks
    )
    app.router.add_get("/v1/default/banks/{bank_id}/documents/{document_id}", document)
    return await start(app)


@pytest.mark.asyncio
async def test_inspector_reads_only_hindsight_data():
    hindsight_runner, hindsight_url = await hindsight_server()
    inspector_runner, inspector_url = await start(
        create_app(InspectorSettings(memory_url=hindsight_url))
    )
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{inspector_url}/health") as response:
                assert response.status == 200
                assert await response.json() == {"status": "ok"}

            async with session.get(f"{inspector_url}/api/banks") as response:
                banks = await response.json()
                assert response.status == 200
                assert response.headers["Cache-Control"] == "no-store"
                assert response.headers["X-Frame-Options"] == "DENY"
            assert banks["items"][0]["name"] == (
                "Engineering <script>alert(1)</script>"
            )
            assert set(banks["items"][0]).isdisjoint(
                {"display_name", "enabled", "receipt_count", "dream"}
            )

            bank_id = "client%3Ascope%3Aone"
            async with session.get(f"{inspector_url}/api/banks/{bank_id}") as response:
                detail = await response.json()
                assert response.status == 200
            assert detail["memories"]["items"][0]["id"] == "memory-1"
            assert detail["stats"]["total_documents"] == 1
            assert set(detail).isdisjoint(
                {
                    "actor_labels",
                    "display_name",
                    "enabled",
                    "receipt_count",
                    "dream",
                }
            )

            document_id = "conversation%3A41"
            async with session.get(
                f"{inspector_url}/api/banks/{bank_id}/documents/{document_id}"
            ) as response:
                source = await response.json()
                assert response.status == 200
            assert source["document"]["original_text"] == "Untrusted episode source"
            assert source["chunks"]["items"][0]["chunk_text"] == "Source chunk"

            async with session.get(
                f"{inspector_url}/api/banks/{bank_id}/memories/memory-1"
            ) as response:
                evidence = await response.json()
                assert response.status == 200
            assert evidence["memory"]["type"] == "observation"
            assert evidence["history"][0]["state"] == "invalidated"
    finally:
        await inspector_runner.cleanup()
        await hindsight_runner.cleanup()


@pytest.mark.asyncio
async def test_inspector_security_and_empty_state():
    hindsight = web.Application()

    async def health(_):
        return web.json_response({"status": "healthy"})

    async def banks(_):
        return web.json_response({"banks": []})

    hindsight.router.add_get("/health", health)
    hindsight.router.add_get("/v1/default/banks", banks)
    hindsight_runner, hindsight_url = await start(hindsight)
    inspector_runner, inspector_url = await start(
        create_app(InspectorSettings(memory_url=hindsight_url))
    )
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{inspector_url}/api/banks") as response:
                assert await response.json() == {"items": [], "total": 0}
            async with session.get(f"{inspector_url}/") as response:
                html = await response.text()
                assert response.headers["Referrer-Policy"] == "no-referrer"
                assert "Memory Inspector" in html
            async with session.get(f"{inspector_url}/app.js") as response:
                script = await response.text()
                assert "innerHTML" not in script
                assert "textContent" in script
                assert "openMemory" in script
                assert "dream" not in script.lower()
                assert "telegram" not in script.lower()
            async with session.get(f"{inspector_url}/api/banks/..%2Fother") as response:
                assert response.status in {400, 404}

            for host in (
                "localhost",
                "localhost:8765",
                "127.0.0.1",
                "[::1]:8765",
            ):
                async with session.get(
                    f"{inspector_url}/", headers={"Host": host}
                ) as response:
                    assert response.status == 200

            for host in ("example.com", "localhost.example.com", "127.0.0.2"):
                async with session.get(
                    f"{inspector_url}/", headers={"Host": host}
                ) as response:
                    assert response.status == 400
    finally:
        await inspector_runner.cleanup()
        await hindsight_runner.cleanup()
