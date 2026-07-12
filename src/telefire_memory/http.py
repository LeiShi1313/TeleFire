from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from aiohttp import web

from telefire_memory.core import MemoryCore


CORE_KEY = web.AppKey("memory_core", MemoryCore)
_DASHBOARD_PATH = Path(__file__).with_name("dashboard")
_PRIVATE_HEADERS = {
    "Cache-Control": "no-store",
    "Content-Security-Policy": (
        "default-src 'self'; base-uri 'none'; connect-src 'self'; "
        "font-src 'self'; form-action 'none'; frame-ancestors 'none'; "
        "img-src 'self'; object-src 'none'; script-src 'self'; style-src 'self'"
    ),
    "Cross-Origin-Resource-Policy": "same-origin",
    "Permissions-Policy": "camera=(), geolocation=(), microphone=()",
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
}


@web.middleware
async def _private_response_headers(
    request: web.Request,
    handler: Any,
) -> web.StreamResponse:
    response = await handler(request)
    response.headers.update(_PRIVATE_HEADERS)
    return response


def create_app(core: MemoryCore) -> web.Application:
    app = web.Application(
        client_max_size=256 * 1024,
        middlewares=[_private_response_headers],
    )
    app[CORE_KEY] = core
    app.router.add_get("/health", _health)
    app.router.add_post("/v1/memory/ingest", _ingest)
    app.router.add_post("/v1/memory/augment", _augment)
    app.router.add_post("/v1/memory/revise", _revise)
    app.router.add_post("/v1/memory/identities", _upsert_identities)
    app.router.add_get("/v1/memory/subjects", _list_subjects)
    app.router.add_get(
        "/v1/memory/subjects/{subject_id}/records",
        _list_records,
    )
    app.router.add_get("/v1/memory/subjects/{subject_id}", _get_subject)
    app.router.add_get("/admin", _dashboard)
    app.router.add_get("/admin/", _dashboard)
    app.router.add_get("/admin/dashboard.css", _dashboard_stylesheet)
    app.router.add_get("/admin/dashboard.js", _dashboard_script)
    app.router.add_get("/admin/ai-flow", _ai_flow)
    app.router.add_get("/admin/ai-flow.css", _ai_flow_stylesheet)
    app.router.add_get("/admin/ai-flow.js", _ai_flow_script)
    app.router.add_get("/favicon.ico", _favicon)
    return app


async def _health(request: web.Request) -> web.Response:
    return web.json_response({"status": "ok"})


async def _dashboard(request: web.Request) -> web.FileResponse:
    return web.FileResponse(_DASHBOARD_PATH / "index.html")


async def _dashboard_stylesheet(request: web.Request) -> web.FileResponse:
    return web.FileResponse(_DASHBOARD_PATH / "dashboard.css")


async def _dashboard_script(request: web.Request) -> web.FileResponse:
    return web.FileResponse(_DASHBOARD_PATH / "dashboard.js")


async def _ai_flow(request: web.Request) -> web.FileResponse:
    return web.FileResponse(_DASHBOARD_PATH / "ai-flow.html")


async def _ai_flow_stylesheet(request: web.Request) -> web.FileResponse:
    return web.FileResponse(_DASHBOARD_PATH / "ai-flow.css")


async def _ai_flow_script(request: web.Request) -> web.FileResponse:
    return web.FileResponse(_DASHBOARD_PATH / "ai-flow.js")


async def _favicon(request: web.Request) -> web.Response:
    return web.Response(status=204)


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


async def _upsert_identities(request: web.Request) -> web.Response:
    try:
        payload = await _json_object(request)
        items = payload.get("items")
        if not isinstance(items, list) or not 1 <= len(items) <= 100:
            raise ValueError("items must be a list containing 1 to 100 identities")
        identities: dict[str, str] = {}
        for item in items:
            if not isinstance(item, dict):
                raise ValueError("each identity must be a JSON object")
            identities[_required_string(item, "key")] = _required_string(
                item, "display_name"
            )
        updated = await request.app[CORE_KEY].upsert_identities(identities)
        return web.json_response({"updated": updated})
    except (ValueError, TypeError) as exc:
        return web.json_response({"error": str(exc)}, status=400)


async def _list_subjects(request: web.Request) -> web.Response:
    try:
        page = request.app[CORE_KEY].list_subjects(
            limit=_query_integer(request, "limit", 50),
            offset=_query_integer(request, "offset", 0),
        )
        return web.json_response(page.to_dict())
    except (ValueError, TypeError) as exc:
        return web.json_response({"error": str(exc)}, status=400)


async def _get_subject(request: web.Request) -> web.Response:
    try:
        subject = request.app[CORE_KEY].get_subject(request.match_info["subject_id"])
        if subject is None:
            return web.json_response({"error": "subject not found"}, status=404)
        return web.json_response(subject.to_dict())
    except (ValueError, TypeError) as exc:
        return web.json_response({"error": str(exc)}, status=400)


async def _list_records(request: web.Request) -> web.Response:
    try:
        page = request.app[CORE_KEY].list_records(
            request.match_info["subject_id"],
            scope_id=request.query.get("scope_id") or None,
            record_type=request.query.get("record_type") or None,
            status=request.query.get("status", "active"),
            query=request.query.get("query") or None,
            limit=_query_integer(request, "limit", 100),
            offset=_query_integer(request, "offset", 0),
        )
        return web.json_response(page.to_dict())
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


def _query_integer(request: web.Request, name: str, default: int) -> int:
    value = request.query.get(name)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc
