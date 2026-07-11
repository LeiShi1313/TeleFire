import json
from datetime import UTC, datetime

from aiohttp import ClientSession, web
import pytest
import pytest_asyncio

from telefire_memory import (
    EmbeddingConfigurationMismatch,
    MemoryCore,
    MemorySettings,
)
from telefire_memory.http import create_app


class FakeOpenAIProvider:
    def __init__(self):
        self.chat_calls = 0
        self.embedding_calls = 0
        self.runner: web.AppRunner | None = None
        self.base_url = ""

    async def start(self):
        app = web.Application()
        app.router.add_post("/v1/chat/completions", self.chat_completion)
        app.router.add_post("/v1/embeddings", self.embeddings)
        self.runner = web.AppRunner(app)
        await self.runner.setup()
        site = web.TCPSite(self.runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        self.base_url = f"http://127.0.0.1:{port}/v1"
        return self

    async def close(self):
        if self.runner:
            await self.runner.cleanup()

    async def chat_completion(self, request: web.Request):
        self.chat_calls += 1
        payload = await request.json()
        content = payload["messages"][-1]["content"]
        if "malformed extraction" in content:
            result = "not-json"
        elif "tea" in content.lower():
            result = json.dumps(
                {
                    "facts": ["The user likes tea"],
                    "episodes": ["The user discussed tea"],
                }
            )
        else:
            result = json.dumps(
                {
                    "facts": ["The user is interested in vector databases"],
                    "episodes": [
                        "On 2026-07-11, the user asked about vector databases"
                    ],
                }
            )
        return web.json_response(
            {
                "id": "chatcmpl-test",
                "object": "chat.completion",
                "created": 1,
                "model": payload["model"],
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": result},
                        "finish_reason": "stop",
                    }
                ],
            }
        )

    async def embeddings(self, request: web.Request):
        self.embedding_calls += 1
        payload = await request.json()
        inputs = payload["input"]
        if isinstance(inputs, str):
            inputs = [inputs]
        data = []
        for index, text in enumerate(inputs):
            lowered = text.lower()
            if "tea" in lowered:
                vector = [0.0, 1.0, 0.0, 0.0]
            elif "vector" in lowered or "database" in lowered:
                vector = [1.0, 0.0, 0.0, 0.0]
            else:
                vector = [0.0, 0.0, 1.0, 0.0]
            data.append({"object": "embedding", "index": index, "embedding": vector})
        return web.json_response(
            {
                "object": "list",
                "model": payload["model"],
                "data": data,
                "usage": {"prompt_tokens": 1, "total_tokens": 1},
            }
        )


@pytest_asyncio.fixture
async def fake_provider():
    provider = await FakeOpenAIProvider().start()
    try:
        yield provider
    finally:
        await provider.close()


def memory_settings(tmp_path, provider, **overrides):
    values = {
        "store_path": tmp_path / "memory",
        "chat_base_url": provider.base_url,
        "chat_api_key": "chat-test-key",
        "chat_model": "chat-test-model",
        "embedding_base_url": provider.base_url,
        "embedding_api_key": "embedding-test-key",
        "embedding_model": "embedding-test-model",
        "embedding_dimension": 4,
    }
    values.update(overrides)
    return MemorySettings(**values)


@pytest.mark.asyncio
async def test_ingest_retains_observation_derives_memory_and_deduplicates_retry(
    tmp_path, fake_provider
):
    core = MemoryCore(memory_settings(tmp_path, fake_provider))
    occurred_at = datetime(2026, 7, 11, 12, 0, tzinfo=UTC)

    first = await core.ingest(
        subject_id="telegram:user:42",
        scope_id="telegram:chat:7",
        text="Alice asked about vector databases",
        occurred_at=occurred_at,
        metadata={"client": "test", "source": "synthetic"},
    )
    retry = await core.ingest(
        subject_id="telegram:user:42",
        scope_id="telegram:chat:7",
        text="Alice asked about vector databases",
        occurred_at=occurred_at,
        metadata={"source": "synthetic", "client": "test"},
    )

    assert first.created is True
    assert first.facts_added == 1
    assert first.episodes_added == 1
    assert retry.created is False
    assert fake_provider.chat_calls == 1
    assert fake_provider.embedding_calls == 1
    assert core._store.count_records(
        subject_id="telegram:user:42",
        scope_id="telegram:chat:7",
        record_type="observation",
    ) == 1


@pytest.mark.asyncio
async def test_augment_is_hybrid_bounded_and_strictly_scoped(tmp_path, fake_provider):
    core = MemoryCore(memory_settings(tmp_path, fake_provider))
    occurred_at = datetime(2026, 7, 11, 12, 0, tzinfo=UTC)
    await core.ingest(
        "telegram:user:42",
        "telegram:chat:7",
        "Alice asked about vector databases",
        occurred_at,
    )
    await core.ingest(
        "telegram:user:42",
        "telegram:chat:8",
        "Alice likes tea",
        occurred_at,
    )
    await core.ingest(
        "telegram:user:99",
        "telegram:chat:7",
        "Another user likes tea",
        occurred_at,
    )

    context = await core.augment(
        "telegram:user:42",
        "What databases did I ask about?",
        scope_id="telegram:chat:7",
        max_items=4,
        max_chars=500,
    )

    assert context.subject_id == "telegram:user:42"
    assert context.scope_id == "telegram:chat:7"
    assert context.profile is None
    assert len(context.facts) == 1
    assert len(context.episodes) == 1
    rendered = context.render()
    assert "vector databases" in rendered
    assert "tea" not in rendered
    assert "Observation" not in rendered
    assert len(rendered) <= 500

    unscoped = await core.augment(
        "telegram:user:42",
        "databases",
        scope_id=None,
    )
    assert unscoped.facts == ()
    assert unscoped.episodes == ()


def test_store_rejects_embedding_space_mismatch(tmp_path, fake_provider):
    MemoryCore(memory_settings(tmp_path, fake_provider))

    with pytest.raises(EmbeddingConfigurationMismatch, match="re-embedding"):
        MemoryCore(
            memory_settings(
                tmp_path,
                fake_provider,
                embedding_model="different-model",
            )
        )
    with pytest.raises(EmbeddingConfigurationMismatch, match="re-embedding"):
        MemoryCore(
            memory_settings(
                tmp_path,
                fake_provider,
                embedding_dimension=8,
            )
        )


@pytest.mark.asyncio
async def test_malformed_extraction_does_not_create_partial_memory(
    tmp_path, fake_provider
):
    core = MemoryCore(memory_settings(tmp_path, fake_provider))

    with pytest.raises(ValueError, match="valid JSON"):
        await core.ingest(
            "telegram:user:42",
            "telegram:chat:7",
            "malformed extraction",
            datetime(2026, 7, 11, tzinfo=UTC),
        )

    assert core._store.count_records(
        subject_id="telegram:user:42",
        scope_id="telegram:chat:7",
    ) == 0


@pytest.mark.asyncio
async def test_http_service_matches_direct_core_semantics(tmp_path, fake_provider):
    core = MemoryCore(memory_settings(tmp_path, fake_provider))
    runner = web.AppRunner(create_app(core))
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    try:
        async with ClientSession() as session:
            ingest_response = await session.post(
                f"http://127.0.0.1:{port}/v1/memory/ingest",
                json={
                    "subject_id": "telegram:user:42",
                    "scope_id": "telegram:chat:7",
                    "text": "Alice asked about vector databases",
                    "occurred_at": "2026-07-11T12:00:00Z",
                    "metadata": {"client": "http-test"},
                },
            )
            assert ingest_response.status == 200
            assert (await ingest_response.json())["created"] is True

            augment_response = await session.post(
                f"http://127.0.0.1:{port}/v1/memory/augment",
                json={
                    "subject_id": "telegram:user:42",
                    "scope_id": "telegram:chat:7",
                    "query": "vector databases",
                    "max_items": 4,
                    "max_chars": 500,
                },
            )
            assert augment_response.status == 200
            payload = await augment_response.json()
            assert payload["subject_id"] == "telegram:user:42"
            assert payload["scope_id"] == "telegram:chat:7"
            assert payload["facts"]
            assert payload["episodes"]
    finally:
        await runner.cleanup()
