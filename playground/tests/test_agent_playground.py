from __future__ import annotations

import json

import aiohttp
from aiohttp import web
import pytest

from agent_playground.app import (
    PlaygroundSettings,
    UpstreamUnavailable,
    _parse_pi_event,
    create_app,
)


async def start(app: web.Application) -> tuple[web.AppRunner, str]:
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    return runner, f"http://127.0.0.1:{port}"


async def dependencies() -> tuple[list[web.AppRunner], str, str, dict]:
    received = {
        "recalls": [],
        "runs": [],
        "cancelled": [],
        "session_queries": [],
        "audit_queries": [],
    }

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
                "type": "memory_snapshot",
                "scopeId": payload["memory"]["scopeId"],
                "queries": ["Who owns deploys?"],
                "memories": [
                    {
                        "id": "memory-1",
                        "text": "Alice maintains the deployment pipeline.",
                        "type": "world",
                        "entities": ["Alice", "actor:alice"],
                        "occurredStart": "2026-07-13T12:00:00Z",
                        "occurredEnd": None,
                        "mentionedAt": "2026-07-13T12:01:00Z",
                        "documentId": "conversation:41",
                        "chunkId": "chunk-1",
                    }
                ],
            },
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

    async def sessions(request):
        assert request.headers["Authorization"] == "Bearer private-pi-token"
        received["session_queries"].append(dict(request.query))
        return web.json_response(
            {
                "items": [
                    {
                        "id": "session-1",
                        "name": "Deployment ownership",
                        "createdAt": "2026-07-13T12:00:00.000Z",
                        "modifiedAt": "2026-07-13T12:05:00.000Z",
                        "messageCount": 4,
                        "firstMessage": "Who owns deployment?",
                    }
                ],
                "total": 1,
                "nextCursor": None,
            }
        )

    async def session_detail(request):
        assert request.headers["Authorization"] == "Bearer private-pi-token"
        assert request.match_info["session_id"] == "session-1"
        return web.json_response(
            {
                "id": "session-1",
                "name": "Deployment ownership",
                "createdAt": "2026-07-13T12:00:00.000Z",
                "modifiedAt": "2026-07-13T12:05:00.000Z",
                "messageCount": 4,
                "firstMessage": "Who owns deployment?",
                "header": {"version": 3, "id": "session-1"},
                "leafId": "entry-2",
                "entries": [
                    {
                        "type": "message",
                        "id": "entry-1",
                        "parentId": None,
                        "timestamp": "2026-07-13T12:00:00.000Z",
                        "message": {
                            "role": "user",
                            "content": "Who owns deployment?",
                        },
                    },
                    {
                        "type": "message",
                        "id": "entry-2",
                        "parentId": "entry-1",
                        "timestamp": "2026-07-13T12:00:01.000Z",
                        "message": {
                            "role": "assistant",
                            "content": [{"type": "text", "text": "Alice owns it."}],
                            "usage": {"input": 20, "output": 5},
                        },
                    },
                ],
            }
        )

    async def audits(request):
        assert request.headers["Authorization"] == "Bearer private-pi-token"
        received["audit_queries"].append(dict(request.query))
        return web.json_response(
            {
                "items": [
                    {
                        "runId": "11111111-1111-4111-8111-111111111111",
                        "sessionId": "session-1",
                        "entryId": "entry-2",
                        "status": "completed",
                        "startedAt": "2026-07-13T12:00:00.000Z",
                        "finishedAt": "2026-07-13T12:00:01.000Z",
                        "prompt": "Who owns deployment?",
                        "memoryScopeId": "chat:engineering",
                        "eventCount": 4,
                    }
                ],
                "total": 1,
                "nextCursor": None,
            }
        )

    async def audit_detail(request):
        assert request.headers["Authorization"] == "Bearer private-pi-token"
        run_id = request.match_info["run_id"]
        return web.json_response(
            {
                "runId": run_id,
                "events": [
                    {
                        "version": 1,
                        "sequence": 1,
                        "timestamp": "2026-07-13T12:00:00.000Z",
                        "runId": run_id,
                        "type": "memory.http.request",
                        "data": {
                            "exchangeId": "exchange-1",
                            "operation": "recall",
                            "request": {
                                "method": "POST",
                                "url": "http://memory/v1/default/banks/chat/memories/recall",
                                "body": {"query": "Who owns deployment?"},
                            },
                        },
                    },
                    {
                        "version": 1,
                        "sequence": 2,
                        "timestamp": "2026-07-13T12:00:01.000Z",
                        "runId": run_id,
                        "type": "tool.completed",
                        "data": {
                            "toolCallId": "call-1",
                            "toolName": "memory_reflect",
                            "isError": False,
                            "durationMs": 42,
                            "result": {"content": [{"type": "text", "text": "Alice"}]},
                        },
                    },
                ],
            }
        )

    pi.router.add_get("/health", pi_health)
    pi.router.add_post("/v1/runs", run)
    pi.router.add_post("/v1/runs/{run_id}/cancel", cancel)
    pi.router.add_get("/v1/sessions", sessions)
    pi.router.add_get("/v1/sessions/{session_id}", session_detail)
    pi.router.add_get("/v1/runs", audits)
    pi.router.add_get("/v1/runs/{run_id}/audit", audit_detail)
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
            "status": "pending",
        }
        assert prepared["request"]["systemPrompt"] == "Use evidence carefully."
        assert events[-1]["type"] == "run_completed"
        assert events[-1]["answer"] == "Alice owns it."
        assert events[1]["type"] == "memory_snapshot"
        assert events[1]["memories"][0]["id"] == "memory-1"

        assert len(received["recalls"]) == 1
        pi_request = received["runs"][0]
        assert pi_request["toolPolicy"] == "owner"
        assert pi_request["memory"] == {
            "scopeId": "chat:engineering",
            "anchors": [],
        }
        assert pi_request["includeMemorySnapshot"] is True
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
async def test_session_history_and_run_audits_are_proxied_without_exposing_token():
    dependency_runners, memory_url, pi_url, received = await dependencies()
    playground_runner, playground_url = await request_app(memory_url, pi_url)
    run_id = "11111111-1111-4111-8111-111111111111"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{playground_url}/api/sessions",
                params={"limit": "20", "q": "deploy"},
            ) as response:
                sessions = await response.json()
                assert response.status == 200
            async with session.get(
                f"{playground_url}/api/sessions/session-1"
            ) as response:
                detail = await response.json()
                assert response.status == 200
            async with session.get(
                f"{playground_url}/api/audits",
                params={"limit": "10", "sessionId": "session-1"},
            ) as response:
                audits = await response.json()
                assert response.status == 200
            async with session.get(
                f"{playground_url}/api/audits/{run_id}"
            ) as response:
                audit = await response.json()
                assert response.status == 200

        assert sessions["items"][0]["id"] == "session-1"
        assert detail["leafId"] == "entry-2"
        assert detail["entries"][1]["message"]["usage"]["input"] == 20
        assert audits["items"][0]["eventCount"] == 4
        assert audit["events"][0]["data"]["request"]["body"]["query"] == (
            "Who owns deployment?"
        )
        assert audit["events"][1]["data"]["result"]["content"][0]["text"] == (
            "Alice"
        )
        assert received["session_queries"] == [{"limit": "20", "q": "deploy"}]
        assert received["audit_queries"] == [
            {"limit": "10", "sessionId": "session-1"}
        ]
        assert "private-pi-token" not in json.dumps(
            {"sessions": sessions, "detail": detail, "audits": audits, "audit": audit}
        )
    finally:
        await playground_runner.cleanup()
        for runner in dependency_runners:
            await runner.cleanup()


@pytest.mark.asyncio
async def test_history_proxy_rejects_malformed_identifiers_and_queries():
    dependency_runners, memory_url, pi_url, received = await dependencies()
    playground_runner, playground_url = await request_app(memory_url, pi_url)
    try:
        async with aiohttp.ClientSession() as session:
            for path in (
                "/api/sessions?limit=0",
                "/api/sessions?limit=101",
                f"/api/sessions?q={'x' * 201}",
                "/api/sessions/%2e%2e%2fsecret",
                "/api/audits?sessionId=../../secret",
                "/api/audits/not-a-run-id",
            ):
                async with session.get(f"{playground_url}{path}") as response:
                    assert response.status in {400, 404}

        assert received["session_queries"] == []
        assert received["audit_queries"] == []
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

            for host in (
                "localhost",
                "sessions.telefire.localhost",
                "sessions.telefire.localhost:18865",
            ):
                async with session.get(
                    f"{playground_url}/", headers={"Host": host}
                ) as response:
                    assert response.status == 200

            for host in (
                "example.com",
                "localhost.example.com",
                "sessions.telefire.localhost.example.com",
                "-invalid.localhost",
                "invalid-.localhost",
            ):
                async with session.get(
                    f"{playground_url}/", headers={"Host": host}
                ) as response:
                    assert response.status == 400

            async with session.get(f"{playground_url}/app.js") as response:
                script = await response.text()
                assert "innerHTML" not in script
                assert "textContent" in script
                assert 'if (event.type === "run_started") return;' in script
                assert 'if (event.type === "memory_snapshot")' in script
                assert (
                    "state.sessionId = event.sessionId || state.sessionId" not in script
                )
                assert "elements.newChat.disabled = running" in script
    finally:
        assert received["runs"] == []
        await playground_runner.cleanup()
        for runner in dependency_runners:
            await runner.cleanup()


@pytest.mark.parametrize(
    "event",
    (
        {},
        {
            "type": "memory_snapshot",
            "scopeId": "chat:engineering",
            "queries": ["Who owns deploys?"],
            "memories": [
                {
                    "id": "memory-1",
                    "text": "Alice maintains the deployment pipeline.",
                    "entities": ["Alice"],
                    "documentId": "not a valid source id",
                }
            ],
        },
    ),
)
def test_playground_rejects_malformed_pi_memory_events(event):
    with pytest.raises(UpstreamUnavailable, match="malformed events"):
        _parse_pi_event(json.dumps(event).encode())
