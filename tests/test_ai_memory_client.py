import asyncio
from datetime import UTC, datetime

from aiohttp import web
import pytest

from telefire.ai_memory import HTTPMemoryClient, MemoryClientError


async def start_server(augment_handler, ingest_handler=None):
    async def default_ingest(request):
        await request.json()
        return web.json_response(
            {"created": True, "facts_added": 1, "episodes_added": 0}
        )

    app = web.Application()
    app.router.add_post("/v1/memory/augment", augment_handler)
    app.router.add_post(
        "/v1/memory/ingest",
        ingest_handler or default_ingest,
    )
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    return runner, f"http://127.0.0.1:{port}"


@pytest.mark.asyncio
async def test_http_memory_client_validates_and_uses_platform_neutral_contract():
    received = {}

    async def augment(request):
        received["augment"] = await request.json()
        return web.json_response(
            {
                "subject_id": "telegram:user:10",
                "scope_id": "telegram:chat:-1001",
                "profile": "# Profile",
                "facts": [],
                "episodes": [],
                "rendered": "Subject profile:\n# Profile",
            }
        )

    async def ingest(request):
        received["ingest"] = await request.json()
        return web.json_response(
            {"created": False, "facts_added": 0, "episodes_added": 0}
        )

    runner, url = await start_server(augment, ingest)
    client = HTTPMemoryClient(url)
    try:
        rendered = await client.augment(
            subject_id="telegram:user:10",
            query="profile",
            scope_id="telegram:chat:-1001",
        )
        await client.ingest(
            subject_id="telegram:user:10",
            scope_id="telegram:chat:-1001",
            text="A synthetic observation",
            occurred_at=datetime(2026, 7, 11, tzinfo=UTC),
            metadata={"client": "test"},
        )

        assert rendered == "Subject profile:\n# Profile"
        assert received["augment"] == {
            "subject_id": "telegram:user:10",
            "scope_id": "telegram:chat:-1001",
            "query": "profile",
        }
        assert "message_id" not in received["ingest"]["metadata"]
    finally:
        await client.close()
        await runner.cleanup()


@pytest.mark.asyncio
async def test_http_memory_client_rejects_malformed_augmentation():
    async def malformed(request):
        await request.json()
        return web.json_response({"rendered": ["not text"]})

    runner, url = await start_server(malformed)
    client = HTTPMemoryClient(url)
    try:
        with pytest.raises(MemoryClientError, match="malformed"):
            await client.augment(
                subject_id="telegram:user:10",
                query="profile",
                scope_id="telegram:chat:-1001",
            )
    finally:
        await client.close()
        await runner.cleanup()


@pytest.mark.asyncio
async def test_http_memory_client_enforces_timeout():
    async def slow(request):
        await request.json()
        await asyncio.sleep(0.1)
        return web.json_response({})

    runner, url = await start_server(slow)
    client = HTTPMemoryClient(url, timeout=0.01)
    try:
        with pytest.raises(TimeoutError):
            await client.augment(
                subject_id="telegram:user:10",
                query="profile",
                scope_id="telegram:chat:-1001",
            )
    finally:
        await client.close()
        await runner.cleanup()
