from __future__ import annotations

import json
from dataclasses import asdict
from typing import Any, Callable
from urllib import error, parse, request

from aiohttp import web

from telefire.ai_dream import DreamCycleBusyError
from telefire.memory_admin import MemoryAdminService
from telefire.onebot.client import OneBotReverseWebSocket


class OneBotMemoryAdminError(RuntimeError):
    pass


def mount_onebot_memory_admin(
    bridge: OneBotReverseWebSocket,
    service: MemoryAdminService,
    *,
    display_name_resolver: Callable[[int], str | None] | None = None,
) -> None:
    async def status(http_request: web.Request) -> web.Response:
        try:
            group_id = _positive_group_id(http_request.query.get("group_id"))
            result = await service.status(group_id)
        except Exception as exc:
            return _error_response(exc)
        return web.json_response(asdict(result))

    async def set_dream(http_request: web.Request) -> web.Response:
        try:
            payload = await _json_object(http_request)
            group_id = _positive_group_id(payload.get("group_id"))
            enabled = payload.get("enabled")
            if not isinstance(enabled, bool):
                raise ValueError("enabled must be a boolean")
            display_name = _optional_display_name(payload.get("display_name"))
            if display_name is None and display_name_resolver is not None:
                display_name = display_name_resolver(group_id)
            result = await service.set_dream(
                group_id,
                enabled=enabled,
                display_name=display_name,
            )
        except Exception as exc:
            return _error_response(exc)
        return web.json_response(asdict(result))

    async def backfill(http_request: web.Request) -> web.Response:
        try:
            payload = await _json_object(http_request)
            group_id = _positive_group_id(payload.get("group_id"))
            mode = payload.get("mode")
            value = payload.get("value")
            if not isinstance(mode, str):
                raise ValueError("mode must be 'days' or 'messages'")
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValueError("value must be an integer")
            result = await service.backfill(
                group_id,
                mode=mode,
                value=value,
            )
        except Exception as exc:
            return _error_response(exc)
        return web.json_response(asdict(result))

    bridge.add_authenticated_route("GET", "/admin/memory/status", status)
    bridge.add_authenticated_route("POST", "/admin/memory/dream", set_dream)
    bridge.add_authenticated_route("POST", "/admin/memory/backfill", backfill)


class OneBotMemoryAdminClient:
    def __init__(
        self,
        base_url: str,
        *,
        token: str,
        self_id: int,
        timeout: float = 900,
    ) -> None:
        if not base_url.strip():
            raise ValueError("OneBot admin URL cannot be empty")
        if not token:
            raise ValueError("OneBot token cannot be empty")
        if self_id <= 0:
            raise ValueError("OneBot self ID must be positive")
        if timeout <= 0:
            raise ValueError("OneBot admin timeout must be positive")
        self._base_url = base_url.rstrip("/")
        self._token = token
        self._self_id = self_id
        self._timeout = timeout

    def status(self, group_id: int) -> dict[str, Any]:
        query = parse.urlencode({"group_id": group_id})
        return self._request("GET", f"/admin/memory/status?{query}")

    def set_dream(
        self,
        group_id: int,
        *,
        enabled: bool,
        display_name: str | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "group_id": group_id,
            "enabled": enabled,
        }
        if display_name:
            payload["display_name"] = display_name
        return self._request("POST", "/admin/memory/dream", payload)

    def backfill(
        self,
        group_id: int,
        *,
        mode: str,
        value: int,
    ) -> dict[str, Any]:
        return self._request(
            "POST",
            "/admin/memory/backfill",
            {"group_id": group_id, "mode": mode, "value": value},
        )

    def _request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        body = (
            json.dumps(payload, ensure_ascii=False).encode("utf-8")
            if payload is not None
            else None
        )
        http_request = request.Request(
            f"{self._base_url}{path}",
            data=body,
            method=method,
            headers={
                "Authorization": f"Bearer {self._token}",
                "Content-Type": "application/json",
                "X-Self-ID": str(self._self_id),
            },
        )
        try:
            with request.urlopen(http_request, timeout=self._timeout) as response:
                decoded = json.load(response)
        except error.HTTPError as exc:
            try:
                failure = json.load(exc)
                message = failure.get("error")
            except (json.JSONDecodeError, AttributeError, UnicodeDecodeError):
                message = None
            raise OneBotMemoryAdminError(
                str(message or f"OneBot memory admin failed with HTTP {exc.code}")
            ) from exc
        except error.URLError as exc:
            raise OneBotMemoryAdminError(
                f"Unable to reach OneBot memory admin at {self._base_url}: {exc.reason}"
            ) from exc
        if not isinstance(decoded, dict):
            raise OneBotMemoryAdminError("OneBot memory admin returned invalid JSON")
        return decoded


async def _json_object(http_request: web.Request) -> dict[str, Any]:
    try:
        payload = await http_request.json()
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ValueError("request body must be a JSON object") from exc
    if not isinstance(payload, dict):
        raise ValueError("request body must be a JSON object")
    return payload


def _positive_group_id(value: Any) -> int:
    if isinstance(value, bool):
        raise ValueError("group_id must be a positive integer")
    if isinstance(value, int):
        group_id = value
    elif isinstance(value, str) and value.isascii() and value.isdecimal():
        group_id = int(value)
    else:
        raise ValueError("group_id must be a positive integer")
    if group_id <= 0:
        raise ValueError("group_id must be a positive integer")
    return group_id


def _optional_display_name(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("display_name must be text")
    normalized = " ".join(value.split())
    if not normalized:
        return None
    if len(normalized) > 256:
        raise ValueError("display_name cannot exceed 256 characters")
    return normalized


def _error_response(exc: Exception) -> web.Response:
    if isinstance(exc, (ValueError, TypeError)):
        status = 400
    elif isinstance(exc, DreamCycleBusyError):
        status = 409
    else:
        status = 500
    message = " ".join(str(exc).split())[:500] or type(exc).__name__
    return web.json_response(
        {"error": message, "type": type(exc).__name__},
        status=status,
    )
