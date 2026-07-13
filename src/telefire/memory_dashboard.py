from __future__ import annotations

import argparse
import asyncio
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote

import aiohttp
import aiosqlite
from aiohttp import web


_STATIC_PATH = Path(__file__).with_name("memory_dashboard_assets")
_BANK_RE = re.compile(r"^[A-Za-z0-9:_-]{1,256}$")
_DOCUMENT_RE = re.compile(r"^[A-Za-z0-9:_.-]{1,512}$")
_MEMORY_RE = re.compile(r"^[A-Za-z0-9_-]{1,256}$")
_HOST_RE = re.compile(
    r"^(?:localhost|127\.0\.0\.1|\[::1\])(?::(?P<port>[0-9]{1,5}))?$",
    re.IGNORECASE,
)
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


@dataclass(frozen=True, slots=True)
class DashboardSettings:
    hindsight_url: str = "http://127.0.0.1:18888"
    state_path: Path = Path.home() / ".telefire" / "ai.db"
    request_timeout: float = 20

    @classmethod
    def from_env(cls) -> DashboardSettings:
        return cls(
            hindsight_url=os.environ.get(
                "TELEFIRE_HINDSIGHT_URL",
                "http://127.0.0.1:18888",
            )
            .strip()
            .rstrip("/"),
            state_path=Path(
                os.environ.get(
                    "TELEFIRE_AI_STATE_PATH",
                    Path.home() / ".telefire" / "ai.db",
                )
            ),
            request_timeout=float(os.environ.get("TELEFIRE_HINDSIGHT_TIMEOUT", "20")),
        )


class DashboardData:
    def __init__(self, settings: DashboardSettings):
        self._settings = settings
        self._session: aiohttp.ClientSession | None = None

    async def close(self) -> None:
        if self._session is not None:
            await self._session.close()
            self._session = None

    async def health(self) -> bool:
        try:
            payload, _ = await asyncio.gather(
                self._hindsight("/health"),
                self._operational(required=True),
            )
        except Exception:
            return False
        return payload.get("status") in {"ok", "healthy"}

    async def banks(self) -> dict[str, Any]:
        remote, operational = await asyncio.gather(
            self._hindsight("/v1/default/banks"),
            self._operational(),
        )
        banks = {
            item["bank_id"]: dict(item)
            for item in remote.get("banks", [])
            if isinstance(item, dict) and isinstance(item.get("bank_id"), str)
        }
        all_ids = set(banks) | set(operational)
        items = []
        for bank_id in sorted(all_ids):
            item = banks.get(bank_id, {"bank_id": bank_id})
            state = operational.get(bank_id, {})
            items.append(
                {
                    **item,
                    "display_name": state.get("display_name"),
                    "enabled": state.get("enabled"),
                    "receipt_count": state.get("receipt_count"),
                    "dream": state.get("dream"),
                }
            )
        return {"items": items, "total": len(items)}

    async def bank(self, bank_id: str) -> dict[str, Any]:
        _validate_identifier(bank_id, _BANK_RE, "bank")
        encoded = quote(bank_id, safe="")
        paths = {
            "memories": f"/v1/default/banks/{encoded}/memories/list?limit=100",
            "documents": f"/v1/default/banks/{encoded}/documents?limit=100",
            "entities": f"/v1/default/banks/{encoded}/entities?limit=100",
            "observations": f"/v1/default/banks/{encoded}/observations/scopes",
        }
        results = await asyncio.gather(
            *(self._hindsight(path) for path in paths.values()),
            return_exceptions=True,
        )
        content: dict[str, Any] = {}
        errors: dict[str, str] = {}
        for name, result in zip(paths, results, strict=True):
            if isinstance(result, Exception):
                content[name] = {"items": [], "total": 0}
                errors[name] = "unavailable"
            else:
                content[name] = result
        operational = (await self._operational()).get(bank_id, {})
        actor_labels = await self._actor_labels(bank_id)
        return {
            "bank_id": bank_id,
            "display_name": operational.get("display_name"),
            "enabled": operational.get("enabled"),
            "receipt_count": operational.get("receipt_count"),
            "dream": operational.get("dream"),
            "actor_labels": actor_labels,
            **content,
            "errors": errors,
        }

    async def document(self, bank_id: str, document_id: str) -> dict[str, Any]:
        _validate_identifier(bank_id, _BANK_RE, "bank")
        _validate_identifier(document_id, _DOCUMENT_RE, "document")
        bank = quote(bank_id, safe="")
        document = quote(document_id, safe="")
        source, chunks = await asyncio.gather(
            self._hindsight(f"/v1/default/banks/{bank}/documents/{document}"),
            self._hindsight(
                f"/v1/default/banks/{bank}/documents/{document}/chunks?limit=100"
            ),
        )
        if source.get("bank_id") != bank_id or source.get("id") != document_id:
            raise web.HTTPBadGateway(text="Hindsight returned mismatched source")
        return {"document": source, "chunks": chunks}

    async def memory(self, bank_id: str, memory_id: str) -> dict[str, Any]:
        _validate_identifier(bank_id, _BANK_RE, "bank")
        _validate_identifier(memory_id, _MEMORY_RE, "memory")
        bank = quote(bank_id, safe="")
        memory = quote(memory_id, safe="")
        detail = await self._hindsight(f"/v1/default/banks/{bank}/memories/{memory}")
        history = detail.get("history") or []
        if detail.get("id") != memory_id or not isinstance(history, list):
            raise web.HTTPBadGateway(text="Hindsight returned mismatched memory")
        return {"memory": detail, "history": history[:100]}

    async def _hindsight(self, path: str) -> dict[str, Any]:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=self._settings.request_timeout)
            )
        async with self._session.get(
            f"{self._settings.hindsight_url}{path}"
        ) as response:
            if response.status < 200 or response.status >= 300:
                raise RuntimeError(f"Hindsight returned HTTP {response.status}")
            payload = await response.json()
        if not isinstance(payload, dict):
            raise RuntimeError("Hindsight returned malformed data")
        return payload

    async def _state_connection(self) -> aiosqlite.Connection:
        if not self._settings.state_path.is_file():
            raise FileNotFoundError(self._settings.state_path)
        connection = await aiosqlite.connect(
            f"file:{self._settings.state_path}?mode=ro",
            uri=True,
        )
        connection.row_factory = aiosqlite.Row
        return connection

    async def _operational(
        self,
        *,
        required: bool = False,
    ) -> dict[str, dict[str, Any]]:
        try:
            connection = await self._state_connection()
            try:
                labels = await _rows(
                    connection,
                    "SELECT scope_id, display_name FROM ai_memory_scope_labels",
                )
                scopes = await _rows(
                    connection,
                    "SELECT scope_id, enabled, display_name FROM ai_memory_scopes",
                )
                receipts = await _rows(
                    connection,
                    "SELECT scope_id, COUNT(*) AS count FROM ai_memory_documents "
                    "GROUP BY scope_id",
                )
                dreams = await _rows(
                    connection,
                    "SELECT scope_id, cursor_message_id, scanned_until_at, last_attempt_at, "
                    "last_success_at, last_error FROM ai_memory_dream_state",
                )
                if required:
                    await _rows(
                        connection,
                        "SELECT scope_id, actor_id, display_name "
                        "FROM ai_memory_actor_labels LIMIT 0",
                    )
            finally:
                await connection.close()
        except (OSError, aiosqlite.Error):
            if required:
                raise
            return {}
        state: dict[str, dict[str, Any]] = {}
        for row in labels:
            state.setdefault(row["scope_id"], {})["display_name"] = row["display_name"]
        for row in scopes:
            item = state.setdefault(row["scope_id"], {})
            item["enabled"] = bool(row["enabled"])
            item.setdefault("display_name", row["display_name"])
        for row in receipts:
            state.setdefault(row["scope_id"], {})["receipt_count"] = row["count"]
        for row in dreams:
            state.setdefault(row["scope_id"], {})["dream"] = {
                "cursor_message_id": row["cursor_message_id"],
                "scanned_until_at": row["scanned_until_at"],
                "last_attempt_at": row["last_attempt_at"],
                "last_success_at": row["last_success_at"],
                "last_error": row["last_error"],
            }
        return state

    async def _actor_labels(self, bank_id: str) -> dict[str, str]:
        try:
            connection = await self._state_connection()
            try:
                rows = await _rows(
                    connection,
                    "SELECT actor_id, display_name FROM ai_memory_actor_labels "
                    "WHERE scope_id = ? ORDER BY actor_id",
                    (bank_id,),
                )
            finally:
                await connection.close()
        except (OSError, aiosqlite.Error):
            return {}
        return {row["actor_id"]: row["display_name"] for row in rows}


async def _rows(
    connection: aiosqlite.Connection,
    query: str,
    parameters: tuple[Any, ...] = (),
) -> list[aiosqlite.Row]:
    cursor = await connection.execute(query, parameters)
    return [row async for row in cursor]


def _validate_identifier(value: str, pattern: re.Pattern[str], kind: str) -> None:
    if not pattern.fullmatch(value):
        raise web.HTTPBadRequest(text=f"Invalid {kind} identity")


def _valid_host(value: str) -> bool:
    match = _HOST_RE.fullmatch(value)
    if match is None:
        return False
    port = match.group("port")
    return port is None or 1 <= int(port) <= 65535


DATA_KEY = web.AppKey("dashboard_data", DashboardData)


@web.middleware
async def _private_headers(
    request: web.Request,
    handler: Any,
) -> web.StreamResponse:
    hosts = request.headers.getall("Host", [])
    if len(hosts) != 1 or not _valid_host(hosts[0]):
        response = web.Response(status=400, text="Invalid Host header")
    else:
        response = await handler(request)
    response.headers.update(_PRIVATE_HEADERS)
    return response


async def _close_data(app: web.Application) -> None:
    await app[DATA_KEY].close()


async def _favicon(_: web.Request) -> web.Response:
    return web.Response(status=204)


def create_app(settings: DashboardSettings | None = None) -> web.Application:
    app = web.Application(
        client_max_size=64 * 1024,
        middlewares=[_private_headers],
    )
    app[DATA_KEY] = DashboardData(settings or DashboardSettings.from_env())
    app.on_cleanup.append(_close_data)
    app.router.add_get("/health", _health)
    app.router.add_get("/api/banks", _banks)
    app.router.add_get("/api/banks/{bank_id}", _bank)
    app.router.add_get(
        "/api/banks/{bank_id}/documents/{document_id}",
        _document,
    )
    app.router.add_get(
        "/api/banks/{bank_id}/memories/{memory_id}",
        _memory,
    )
    app.router.add_get("/", _index)
    app.router.add_get("/admin", _index)
    app.router.add_get("/admin/", _index)
    app.router.add_get("/app.js", _script)
    app.router.add_get("/styles.css", _styles)
    app.router.add_get("/favicon.ico", _favicon)
    return app


async def _health(request: web.Request) -> web.Response:
    healthy = await request.app[DATA_KEY].health()
    return web.json_response(
        {"status": "ok" if healthy else "degraded"},
        status=200 if healthy else 503,
    )


async def _banks(request: web.Request) -> web.Response:
    try:
        return web.json_response(await request.app[DATA_KEY].banks())
    except Exception as exc:
        raise web.HTTPBadGateway(text="Memory inspection unavailable") from exc


async def _bank(request: web.Request) -> web.Response:
    try:
        payload = await request.app[DATA_KEY].bank(request.match_info["bank_id"])
        return web.json_response(payload)
    except web.HTTPException:
        raise
    except Exception as exc:
        raise web.HTTPBadGateway(text="Memory bank unavailable") from exc


async def _document(request: web.Request) -> web.Response:
    try:
        payload = await request.app[DATA_KEY].document(
            request.match_info["bank_id"],
            request.match_info["document_id"],
        )
        return web.json_response(payload)
    except web.HTTPException:
        raise
    except Exception as exc:
        raise web.HTTPBadGateway(text="Memory source unavailable") from exc


async def _memory(request: web.Request) -> web.Response:
    try:
        payload = await request.app[DATA_KEY].memory(
            request.match_info["bank_id"],
            request.match_info["memory_id"],
        )
        return web.json_response(payload)
    except web.HTTPException:
        raise
    except Exception as exc:
        raise web.HTTPBadGateway(text="Memory evidence unavailable") from exc


async def _index(request: web.Request) -> web.FileResponse:
    return web.FileResponse(_STATIC_PATH / "index.html")


async def _script(request: web.Request) -> web.FileResponse:
    return web.FileResponse(_STATIC_PATH / "app.js")


async def _styles(request: web.Request) -> web.FileResponse:
    return web.FileResponse(_STATIC_PATH / "styles.css")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the memory inspection dashboard")
    parser.add_argument(
        "--host",
        default=os.environ.get("TELEFIRE_MEMORY_DASHBOARD_HOST", "127.0.0.1"),
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("TELEFIRE_MEMORY_DASHBOARD_PORT", "8765")),
    )
    args = parser.parse_args()
    web.run_app(create_app(), host=args.host, port=args.port)


if __name__ == "__main__":
    main()
