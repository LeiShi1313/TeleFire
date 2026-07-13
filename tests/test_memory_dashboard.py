from __future__ import annotations

from pathlib import Path
import sqlite3

import aiohttp
from aiohttp import web
import pytest

from telefire.ai import AIStateRepository
from telefire.memory_dashboard import DashboardSettings, create_app


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
                        "bank_id": "telegram:chat:-1001",
                        "fact_count": 2,
                        "last_document_at": "2026-07-13T12:00:00Z",
                    }
                ]
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
                        "entities": ["telegram:user:20"],
                        "document_id": "telegram:thread:-1001:41",
                        "state": "valid",
                    }
                ],
                "total": 1,
                "limit": 100,
                "offset": 0,
            }
        )

    async def memory(request):
        return web.json_response(
            {
                "id": request.match_info["memory_id"],
                "text": "Alice directly stated that the plan changed.",
                "type": "observation",
                "entities": ["telegram:user:20"],
                "state": "valid",
                "occurred_start": "2026-07-13T12:00:00Z",
                "mentioned_at": "2026-07-13T12:01:00Z",
                "source_memories": [
                    {
                        "id": "source-memory-1",
                        "type": "world",
                        "text": "Alice said the plan changed.",
                        "occurred_start": "2026-07-13T12:00:00Z",
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
                        "id": "telegram:thread:-1001:41",
                        "memory_unit_count": 1,
                        "content_hash": "hash-1",
                    }
                ],
                "total": 1,
                "limit": 100,
                "offset": 0,
            }
        )

    async def entities(_):
        return web.json_response(
            {
                "items": [
                    {
                        "id": "entity-1",
                        "canonical_name": "telegram:user:20",
                        "mention_count": 2,
                    }
                ],
                "total": 1,
                "limit": 100,
                "offset": 0,
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
                "content_hash": "hash-1",
                "created_at": "2026-07-13T12:00:00Z",
                "updated_at": "2026-07-13T12:00:00Z",
                "memory_unit_count": 1,
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
                        "created_at": "2026-07-13T12:00:00Z",
                    }
                ],
                "total": 1,
                "limit": 100,
                "offset": 0,
            }
        )

    app.router.add_get("/health", health)
    app.router.add_get("/v1/default/banks", banks)
    app.router.add_get("/v1/default/banks/{bank_id}/memories/list", memories)
    app.router.add_get("/v1/default/banks/{bank_id}/memories/{memory_id}", memory)
    app.router.add_get("/v1/default/banks/{bank_id}/documents", documents)
    app.router.add_get("/v1/default/banks/{bank_id}/entities", entities)
    app.router.add_get(
        "/v1/default/banks/{bank_id}/observations/scopes",
        observations,
    )
    app.router.add_get(
        "/v1/default/banks/{bank_id}/documents/{document_id}/chunks",
        chunks,
    )
    app.router.add_get(
        "/v1/default/banks/{bank_id}/documents/{document_id}",
        document,
    )
    return await start(app)


async def state_database(path: Path) -> None:
    store = await AIStateRepository(path).connect()
    try:
        await store.set_memory_enabled(
            "telegram:chat:-1001",
            True,
            "Fallback Group",
        )
        await store.record_memory_labels(
            "telegram:chat:-1001",
            "Engineering <script>alert(1)</script>",
            {"telegram:user:20": "Alice <b>Unsafe</b>"},
        )
        await store.save_memory_document_receipt(
            "telegram:chat:-1001",
            "telegram:thread:-1001:41",
            "hash-1",
            (("telegram:message:-1001:41", "event-hash"),),
        )
        await store.record_memory_dream_success(
            "telegram:chat:-1001",
            cursor_message_id=41,
            scanned_until_at=100,
            succeeded_at=100,
        )
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_dashboard_merges_bank_memory_and_operational_state(tmp_path):
    hindsight_runner, hindsight_url = await hindsight_server()
    state_path = tmp_path / "ai.db"
    await state_database(state_path)
    dashboard_runner, dashboard_url = await start(
        create_app(
            DashboardSettings(
                hindsight_url=hindsight_url,
                state_path=state_path,
            )
        )
    )
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{dashboard_url}/health") as response:
                assert response.status == 200
                assert await response.json() == {"status": "ok"}

            async with session.get(f"{dashboard_url}/api/banks") as response:
                assert response.status == 200
                banks = await response.json()
                assert response.headers["Cache-Control"] == "no-store"
                assert response.headers["X-Frame-Options"] == "DENY"
            assert banks["items"][0]["display_name"] == (
                "Engineering <script>alert(1)</script>"
            )
            assert banks["items"][0]["enabled"] is True
            assert banks["items"][0]["receipt_count"] == 1

            bank_id = "telegram%3Achat%3A-1001"
            async with session.get(f"{dashboard_url}/api/banks/{bank_id}") as response:
                detail = await response.json()
                assert response.status == 200
            assert detail["actor_labels"] == {"telegram:user:20": "Alice <b>Unsafe</b>"}
            assert detail["memories"]["items"][0]["id"] == "memory-1"
            assert detail["dream"]["cursor_message_id"] == 41
            assert detail["dream"]["scanned_until_at"] == 100

            document_id = "telegram%3Athread%3A-1001%3A41"
            async with session.get(
                f"{dashboard_url}/api/banks/{bank_id}/documents/{document_id}"
            ) as response:
                source = await response.json()
                assert response.status == 200
            assert source["document"]["original_text"] == "Untrusted episode source"
            assert source["chunks"]["items"][0]["chunk_text"] == "Source chunk"

            async with session.get(
                f"{dashboard_url}/api/banks/{bank_id}/memories/memory-1"
            ) as response:
                evidence = await response.json()
                assert response.status == 200
            assert evidence["memory"]["type"] == "observation"
            assert evidence["memory"]["source_memories"][0]["type"] == "world"
            assert evidence["history"][0]["state"] == "invalidated"
    finally:
        await dashboard_runner.cleanup()
        await hindsight_runner.cleanup()


@pytest.mark.parametrize("state_kind", ["missing", "missing-schema", "corrupt"])
@pytest.mark.asyncio
async def test_unavailable_operational_state_degrades_health_not_bank_listing(
    tmp_path,
    state_kind,
):
    hindsight_runner, hindsight_url = await hindsight_server()
    state_path = tmp_path / "ai.db"
    if state_kind == "missing-schema":
        sqlite3.connect(state_path).close()
    elif state_kind == "corrupt":
        state_path.write_text("not a SQLite database")
    dashboard_runner, dashboard_url = await start(
        create_app(
            DashboardSettings(
                hindsight_url=hindsight_url,
                state_path=state_path,
            )
        )
    )
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{dashboard_url}/health") as response:
                assert response.status == 503
                assert await response.json() == {"status": "degraded"}

            async with session.get(f"{dashboard_url}/api/banks") as response:
                assert response.status == 200
                banks = await response.json()
            assert banks["total"] == 1
            assert banks["items"][0]["bank_id"] == "telegram:chat:-1001"
            assert banks["items"][0]["display_name"] is None
            assert banks["items"][0]["enabled"] is None
            assert banks["items"][0]["receipt_count"] is None
            assert banks["items"][0]["dream"] is None

            bank_id = "telegram%3Achat%3A-1001"
            async with session.get(f"{dashboard_url}/api/banks/{bank_id}") as response:
                assert response.status == 200
                detail = await response.json()
            assert detail["display_name"] is None
            assert detail["enabled"] is None
            assert detail["receipt_count"] is None
            assert detail["dream"] is None
    finally:
        await dashboard_runner.cleanup()
        await hindsight_runner.cleanup()


@pytest.mark.asyncio
async def test_dashboard_empty_state_security_and_invalid_identity(tmp_path):
    hindsight = web.Application()

    async def health(_):
        return web.json_response({"status": "healthy"})

    async def banks(_):
        return web.json_response({"banks": []})

    hindsight.router.add_get("/health", health)
    hindsight.router.add_get("/v1/default/banks", banks)
    hindsight_runner, hindsight_url = await start(hindsight)
    dashboard_runner, dashboard_url = await start(
        create_app(
            DashboardSettings(
                hindsight_url=hindsight_url,
                state_path=tmp_path / "missing.db",
            )
        )
    )
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{dashboard_url}/api/banks") as response:
                assert await response.json() == {"items": [], "total": 0}
            async with session.get(f"{dashboard_url}/") as response:
                html = await response.text()
                assert response.headers["Referrer-Policy"] == "no-referrer"
                assert "Memory Inspector" in html
            async with session.get(f"{dashboard_url}/app.js") as response:
                script = await response.text()
                assert "innerHTML" not in script
                assert "textContent" in script
                assert 'return "Unknown"' in script
                assert "data.receipt_count || 0" not in script
                assert 'data.enabled ? "Enabled" : "Disabled"' not in script
                assert "openMemory" in script
                assert "memory.fact_type" in script
            async with session.get(f"{dashboard_url}/styles.css") as response:
                styles = await response.text()
                assert "[hidden] { display: none !important; }" in styles
            async with session.get(f"{dashboard_url}/api/banks/..%2Fother") as response:
                assert response.status in {400, 404}

            for host in (
                "localhost",
                "localhost:8765",
                "LOCALHOST:443",
                "127.0.0.1",
                "127.0.0.1:8765",
                "[::1]",
                "[::1]:8765",
            ):
                async with session.get(
                    f"{dashboard_url}/",
                    headers={"Host": host},
                ) as response:
                    assert response.status == 200

            for host in (
                "example.com",
                "localhost.example.com",
                "localhost:",
                "localhost:0",
                "localhost:65536",
                "127.0.0.2",
                "::1",
                "[::1].example.com",
                "[::1]:not-a-port",
            ):
                async with session.get(
                    f"{dashboard_url}/",
                    headers={"Host": host},
                ) as response:
                    assert response.status == 400

            async with session.get(
                f"{dashboard_url}/",
                headers=(("Host", "localhost"), ("Host", "example.com")),
            ) as response:
                assert response.status == 400
    finally:
        await dashboard_runner.cleanup()
        await hindsight_runner.cleanup()
