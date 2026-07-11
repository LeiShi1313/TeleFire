from __future__ import annotations

from datetime import datetime
from typing import Any

from aiohttp import web

from telefire_memory.core import MemoryCore


CORE_KEY = web.AppKey("memory_core", MemoryCore)


def create_app(core: MemoryCore) -> web.Application:
    app = web.Application(client_max_size=256 * 1024)
    app[CORE_KEY] = core
    app.router.add_get("/health", _health)
    app.router.add_post("/v1/memory/ingest", _ingest)
    app.router.add_post("/v1/memory/augment", _augment)
    app.router.add_post("/v1/memory/revise", _revise)
    return app


async def _health(request: web.Request) -> web.Response:
    return web.json_response({"status": "ok"})


async def _ingest(request: web.Request) -> web.Response:
    try:
        payload = await _json_object(request)
        occurred_at = datetime.fromisoformat(
            _required_string(payload, "occurred_at").replace("Z", "+00:00")
        )
        result = await request.app[CORE_KEY].ingest(
            subject_id=_required_string(payload, "subject_id"),
            scope_id=_required_string(payload, "scope_id"),
            text=_required_string(payload, "text"),
            occurred_at=occurred_at,
            metadata=payload.get("metadata"),
        )
        return web.json_response(result.to_dict())
    except (ValueError, TypeError) as exc:
        return web.json_response({"error": str(exc)}, status=400)


async def _augment(request: web.Request) -> web.Response:
    try:
        payload = await _json_object(request)
        context = await request.app[CORE_KEY].augment(
            subject_id=_required_string(payload, "subject_id"),
            query=_required_string(payload, "query"),
            scope_id=payload.get("scope_id"),
            max_items=int(payload.get("max_items", 8)),
            max_chars=int(payload.get("max_chars", 4_000)),
        )
        return web.json_response(context.to_dict())
    except (ValueError, TypeError) as exc:
        return web.json_response({"error": str(exc)}, status=400)


async def _revise(request: web.Request) -> web.Response:
    try:
        payload = await _json_object(request)
        result = await request.app[CORE_KEY].revise(
            subject_id=_required_string(payload, "subject_id"),
            instruction=_required_string(payload, "instruction"),
            evidence=payload.get("evidence"),
            scope_id=payload.get("scope_id"),
        )
        return web.json_response(result.to_dict())
    except (ValueError, TypeError) as exc:
        return web.json_response({"error": str(exc)}, status=400)


async def _json_object(request: web.Request) -> dict[str, Any]:
    try:
        payload = await request.json()
    except Exception as exc:
        raise ValueError("Request body must be valid JSON") from exc
    if not isinstance(payload, dict):
        raise ValueError("Request body must be a JSON object")
    return payload


def _required_string(payload: dict[str, Any], name: str) -> str:
    value = payload.get(name)
    if not isinstance(value, str) or not value:
        raise ValueError(f"{name} is required")
    return value
