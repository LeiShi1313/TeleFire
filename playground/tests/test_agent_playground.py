from __future__ import annotations

import json

import aiohttp
from aiohttp import web
import pytest

from agent_playground.app import PlaygroundSettings, create_app


async def start(app: web.Application) -> tuple[web.AppRunner, str]:
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    return runner, f"http://127.0.0.1:{port}"


async def dependencies() -> tuple[list[web.AppRunner], str, str, dict]:
    received = {"recalls": [], "runs": [], "cancelled": []}

    memory = web.Application()

    async def memory_health(_):
        return web.json_response({"status": "healthy"})

    async def banks(_):
        return web.json_response(
            {
                "banks": [
                    {
                        "bank_id": "chat:engineering",
                        "name": "Engineering",
                        "fact_count": 12,
                    }
                ]
            }
        )

    async def recall(request):
        received["recalls"].append(await request.json())
        return web.json_response(
            {
                "results": [
                    {
                        "id": "memory-1",
                        "text": "Alice maintains the deployment pipeline.",
                        "type": "world",
                        "entities": ["Alice", "actor:alice"],
                        "occurred_start": "2026-07-13T12:00:00Z",
                        "mentioned_at": "2026-07-13T12:01:00Z",
                        "document_id": "conversation:41",
                        "chunk_id": "chunk-1",
                    }
                ]
            }
        )

    memory.router.add_get("/health", memory_health)
    memory.router.add_get("/v1/default/banks", banks)
    memory.router.add_post("/v1/default/banks/{bank_id}/memories/recall", recall)
    memory_runner, memory_url = await start(memory)

    pi = web.Application()

    async def pi_health(_):
        return web.json_response({"status": "ok"})

    async def run(request):
        assert request.headers["Authorization"] == "Bearer private-pi-token"
        payload = await request.json()
        received["runs"].append(payload)
        response = web.StreamResponse(
            headers={"Content-Type": "application/x-ndjson; charset=utf-8"}
        )
        await response.prepare(request)
        events = (
            {
                "type": "run_started",
                "runId": payload["runId"],
                "sessionId": "session-1",
            },
            {
                "type": "tool_snapshot",
                "phase": "completed",
                "tool": "memory_reflect",
                "summary": "Memory reflection completed",
            },
            {"type": "text_delta", "delta": "Alice owns it.", "reset": True},
            {
                "type": "run_completed",
                "sessionId": "session-1",
                "entryId": "entry-1",
                "answer": "Alice owns it.",
            },
        )
        for event in events:
            await response.write(json.dumps(event).encode() + b"\n")
        await response.write_eof()
        return response

    async def cancel(request):
        assert request.headers["Authorization"] == "Bearer private-pi-token"
        received["cancelled"].append(request.match_info["run_id"])
        return web.json_response({"cancelled": True})

    pi.router.add_get("/health", pi_health)
    pi.router.add_post("/v1/runs", run)
    pi.router.add_post("/v1/runs/{run_id}/cancel", cancel)
    pi_runner, pi_url = await start(pi)
    return [memory_runner, pi_runner], memory_url, pi_url, received


async def request_app(memory_url: str, pi_url: str) -> tuple[web.AppRunner, str]:
    return await start(
        create_app(
            PlaygroundSettings(
                memory_url=memory_url,
                pi_url=pi_url,
                pi_token="private-pi-token",
                system_prompt="Use evidence carefully.",
            )
        )
    )


def test_settings_use_generic_environment_names(monkeypatch):
    monkeypatch.setenv("PI_AGENT_TOKEN", "configured-token")
    monkeypatch.setenv("MEMORY_API_URL", "http://memory.internal:8888/")
    monkeypatch.setenv("PI_AGENT_URL", "http://pi.internal:8790/")

    settings = PlaygroundSettings.from_env()

    assert settings.memory_url == "http://memory.internal:8888"
    assert settings.pi_url == "http://pi.internal:8790"
    assert settings.pi_token == "configured-token"
    assert settings.system_prompt.startswith("You are a helpful assistant")


@pytest.mark.asyncio
async def test_agent_run_delegates_memory_to_pi_and_streams_events():
    dependency_runners, memory_url, pi_url, received = await dependencies()
    playground_runner, playground_url = await request_app(memory_url, pi_url)
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{playground_url}/health") as response:
                assert response.status == 200
                assert await response.json() == {"status": "ok"}

            async with session.get(f"{playground_url}/api/banks") as response:
                banks = await response.json()
            assert banks["items"][0]["name"] == "Engineering"

            async with session.post(
                f"{playground_url}/api/recall",
                json={"bankId": "chat:engineering", "query": "Who owns deploys?"},
            ) as response:
                preview = await response.json()
                assert response.status == 200
            assert preview["memories"][0]["id"] == "memory-1"
            assert "Alice maintains" in preview["context"]

            async with session.post(
                f"{playground_url}/api/runs",
                json={
                    "mode": "agent",
                    "prompt": "Who owns deploys?",
                    "bankId": "chat:engineering",
                    "recallContext": "Earlier we discussed deployment ownership.",
                    "context": "A release is scheduled tomorrow.",
                    "sessionId": None,
                    "parentEntryId": None,
                },
            ) as response:
                assert response.status == 200
                assert response.headers["Content-Type"].startswith(
                    "application/x-ndjson"
                )
                events = [json.loads(line) async for line in response.content]

        prepared = events[0]
        assert prepared["type"] == "run_prepared"
        assert prepared["mode"] == "agent"
        assert prepared["toolPolicy"] == "owner"
        assert prepared["memory"] == {
            "bankId": "chat:engineering",
            "query": "Automatic from request and references",
            "memories": [],
            "managedBy": "agent",
        }
        assert prepared["request"]["systemPrompt"] == "Use evidence carefully."
        assert events[-1]["type"] == "run_completed"
        assert events[-1]["answer"] == "Alice owns it."

        assert len(received["recalls"]) == 1
        pi_request = received["runs"][0]
        assert pi_request["toolPolicy"] == "owner"
        assert pi_request["memory"] == {
            "scopeId": "chat:engineering",
            "anchors": [],
        }
        assert [item["kind"] for item in pi_request["context"]] == [
            "reference",
            "reference",
        ]
        assert "Earlier we discussed" in pi_request["context"][0]["text"]
        assert "private-pi-token" not in json.dumps(events)
    finally:
        await playground_runner.cleanup()
        for runner in dependency_runners:
            await runner.cleanup()


@pytest.mark.asyncio
async def test_llm_mode_disables_tools_and_supports_cancellation():
    dependency_runners, memory_url, pi_url, received = await dependencies()
    playground_runner, playground_url = await request_app(memory_url, pi_url)
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{playground_url}/api/runs",
                json={
                    "mode": "llm",
                    "prompt": "Continue",
                    "bankId": None,
                    "sessionId": "session-1",
                    "parentEntryId": "entry-1",
                },
            ) as response:
                events = [json.loads(line) async for line in response.content]
            run_id = events[0]["runId"]
            async with session.post(
                f"{playground_url}/api/runs/{run_id}/cancel"
            ) as response:
                assert response.status == 200
                assert await response.json() == {"cancelled": True}

        assert received["runs"][0]["toolPolicy"] == "none"
        assert received["runs"][0]["sessionId"] == "session-1"
        assert received["runs"][0]["parentEntryId"] == "entry-1"
        assert received["cancelled"] == [run_id]
    finally:
        await playground_runner.cleanup()
        for runner in dependency_runners:
            await runner.cleanup()


@pytest.mark.asyncio
async def test_playground_rejects_invalid_input_and_untrusted_hosts():
    dependency_runners, memory_url, pi_url, received = await dependencies()
    playground_runner, playground_url = await request_app(memory_url, pi_url)
    try:
        async with aiohttp.ClientSession() as session:
            for payload in (
                {"mode": "raw", "prompt": "hello"},
                {"mode": "agent", "prompt": ""},
                {
                    "mode": "agent",
                    "prompt": "hello",
                    "bankId": "../../internal",
                },
                {
                    "mode": "agent",
                    "prompt": "hello",
                    "sessionId": "session-only",
                    "parentEntryId": None,
                },
            ):
                async with session.post(
                    f"{playground_url}/api/runs", json=payload
                ) as response:
                    assert response.status == 400
                    body = await response.json()
                    assert body["error"]["code"] == "INVALID_REQUEST"

            async with session.get(
                f"{playground_url}/", headers={"Host": "example.com"}
            ) as response:
                assert response.status == 400

            async with session.get(f"{playground_url}/app.js") as response:
                script = await response.text()
                assert "innerHTML" not in script
                assert "textContent" in script
                assert 'if (event.type === "run_started") return;' in script
                assert (
                    "state.sessionId = event.sessionId || state.sessionId" not in script
                )
                assert "elements.newChat.disabled = running" in script
    finally:
        assert received["runs"] == []
        await playground_runner.cleanup()
        for runner in dependency_runners:
            await runner.cleanup()
