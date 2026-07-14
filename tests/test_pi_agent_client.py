from __future__ import annotations

import pytest
from aiohttp import web

from telefire.ai import (
    AgentContext,
    AgentIdentityAnchor,
    AgentMemoryTarget,
    AgentRunRequest,
    PiAgentGateway,
)
from telefire.ai_attachments import AttachmentAnalysisRequest


async def serve(app: web.Application) -> tuple[web.AppRunner, str]:
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    sockets = site._server.sockets
    port = sockets[0].getsockname()[1]
    return runner, f"http://127.0.0.1:{port}"


def run_request() -> AgentRunRequest:
    return AgentRunRequest(
        run_id="11111111-1111-4111-8111-111111111111",
        session_id=None,
        parent_entry_id=None,
        prompt="Calculate 6 * 7",
        context=(AgentContext(kind="reference", text="Prior conversation"),),
        system_prompt="Answer directly.",
        tool_policy="delegated",
        memory=AgentMemoryTarget(
            scope_id="telegram:chat:-1001",
            anchors=(
                AgentIdentityAnchor(
                    identity="telegram:user:40",
                    label="Alice",
                ),
            ),
        ),
    )


@pytest.mark.asyncio
async def test_pi_gateway_streams_validated_ndjson_events() -> None:
    received = None

    async def runs(request: web.Request) -> web.StreamResponse:
        nonlocal received
        assert request.headers["Authorization"] == "Bearer test-agent-token"
        received = await request.json()
        response = web.StreamResponse(headers={"Content-Type": "application/x-ndjson"})
        await response.prepare(request)
        await response.write(
            b'{"type":"run_started","runId":"11111111-1111-4111-8111-111111111111",'
        )
        await response.write(b'"sessionId":"session-1"}\n')
        await response.write(
            b'{"type":"tool_snapshot","phase":"completed","tool":"code_exec",'
            b'"summary":"Calculation result: 42"}\n'
        )
        await response.write(b'{"type":"text_delta","delta":"42","reset":true}\n')
        await response.write(
            b'{"type":"run_completed","sessionId":"session-1",'
            b'"entryId":"entry-1","answer":"42"}\n'
        )
        await response.write_eof()
        return response

    app = web.Application()
    app.router.add_post("/v1/runs", runs)
    runner, base_url = await serve(app)
    gateway = PiAgentGateway(base_url, token="test-agent-token", timeout=5)
    try:
        events = [event async for event in gateway.run(run_request())]
    finally:
        await gateway.close()
        await runner.cleanup()

    assert received == {
        "runId": "11111111-1111-4111-8111-111111111111",
        "sessionId": None,
        "parentEntryId": None,
        "prompt": "Calculate 6 * 7",
        "context": [{"kind": "reference", "text": "Prior conversation"}],
        "systemPrompt": "Answer directly.",
        "toolPolicy": "delegated",
        "memory": {
            "scopeId": "telegram:chat:-1001",
            "anchors": [{"id": "telegram:user:40", "label": "Alice"}],
        },
    }
    assert [event.type for event in events] == [
        "run_started",
        "tool_snapshot",
        "text_delta",
        "run_completed",
    ]
    assert events[1].summary == "Calculation result: 42"
    assert events[-1].session_id == "session-1"
    assert events[-1].entry_id == "entry-1"


@pytest.mark.asyncio
async def test_pi_gateway_rejects_a_malformed_or_incomplete_stream() -> None:
    async def runs(request: web.Request) -> web.Response:
        return web.Response(
            text='{"type":"text_delta","delta":42,"reset":true}\n',
            content_type="application/x-ndjson",
        )

    app = web.Application()
    app.router.add_post("/v1/runs", runs)
    runner, base_url = await serve(app)
    gateway = PiAgentGateway(base_url, token="test-agent-token", timeout=5)
    try:
        with pytest.raises(RuntimeError, match="invalid event"):
            async for _ in gateway.run(run_request()):
                pass
    finally:
        await gateway.close()
        await runner.cleanup()


@pytest.mark.asyncio
async def test_pi_gateway_cancels_by_run_id() -> None:
    cancelled = None

    async def cancel(request: web.Request) -> web.Response:
        nonlocal cancelled
        assert request.headers["Authorization"] == "Bearer test-agent-token"
        cancelled = request.match_info["run_id"]
        return web.json_response({"cancelled": True})

    app = web.Application()
    app.router.add_post("/v1/runs/{run_id}/cancel", cancel)
    runner, base_url = await serve(app)
    gateway = PiAgentGateway(base_url, token="test-agent-token", timeout=5)
    try:
        assert await gateway.cancel(run_request().run_id) is True
    finally:
        await gateway.close()
        await runner.cleanup()

    assert cancelled == run_request().run_id


@pytest.mark.asyncio
async def test_pi_gateway_sends_bounded_attachment_for_description() -> None:
    received = None

    async def describe(request: web.Request) -> web.Response:
        nonlocal received
        assert request.headers["Authorization"] == "Bearer test-agent-token"
        received = await request.json()
        return web.json_response(
            {"description": "Description: a diagram.\nVisible text: API"}
        )

    app = web.Application()
    app.router.add_post("/v1/attachments/describe", describe)
    runner, base_url = await serve(app)
    gateway = PiAgentGateway(base_url, token="test-agent-token", timeout=5)
    try:
        result = await gateway.describe_attachment(
            AttachmentAnalysisRequest(
                kind="image",
                mime_type="image/jpeg",
                filename="diagram.jpg",
                data=b"image-bytes",
            )
        )
    finally:
        await gateway.close()
        await runner.cleanup()

    assert result == "Description: a diagram.\nVisible text: API"
    assert received == {
        "kind": "image",
        "mimeType": "image/jpeg",
        "filename": "diagram.jpg",
        "data": "aW1hZ2UtYnl0ZXM=",
    }
