from __future__ import annotations

from datetime import datetime
from typing import Any, Protocol

import aiohttp


class MemoryClient(Protocol):
    async def augment(
        self,
        *,
        subject_id: str,
        query: str,
        scope_id: str,
    ) -> str: ...

    async def ingest(
        self,
        *,
        subject_id: str,
        scope_id: str,
        text: str,
        occurred_at: datetime,
        metadata: dict[str, Any] | None = None,
    ) -> None: ...


class MemoryClientError(RuntimeError):
    pass


class HTTPMemoryClient:
    def __init__(self, base_url: str, *, timeout: float = 3.0):
        if timeout <= 0:
            raise ValueError("Memory timeout must be positive")
        self._base_url = base_url.rstrip("/")
        if not self._base_url.startswith(("http://", "https://")):
            raise ValueError("Memory URL must use http or https")
        self._timeout = aiohttp.ClientTimeout(total=timeout)
        self._session: aiohttp.ClientSession | None = None

    async def close(self) -> None:
        if self._session is not None:
            await self._session.close()
            self._session = None

    async def augment(
        self,
        *,
        subject_id: str,
        query: str,
        scope_id: str,
    ) -> str:
        payload = await self._post(
            "/v1/memory/augment",
            {
                "subject_id": subject_id,
                "scope_id": scope_id,
                "query": query,
            },
        )
        if (
            payload.get("subject_id") != subject_id
            or payload.get("scope_id") != scope_id
            or not isinstance(payload.get("rendered"), str)
            or len(payload["rendered"]) > 20_000
        ):
            raise MemoryClientError("Memory augmentation response is malformed")
        return payload["rendered"]

    async def ingest(
        self,
        *,
        subject_id: str,
        scope_id: str,
        text: str,
        occurred_at: datetime,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        payload = await self._post(
            "/v1/memory/ingest",
            {
                "subject_id": subject_id,
                "scope_id": scope_id,
                "text": text,
                "occurred_at": occurred_at.isoformat(),
                "metadata": metadata,
            },
        )
        if not isinstance(payload.get("created"), bool):
            raise MemoryClientError("Memory ingest response is malformed")

    async def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        if self._session is None:
            self._session = aiohttp.ClientSession(timeout=self._timeout)
        async with self._session.post(f"{self._base_url}{path}", json=payload) as response:
            if response.status < 200 or response.status >= 300:
                raise MemoryClientError(
                    f"Memory service request failed with status {response.status}"
                )
            try:
                result = await response.json()
            except (aiohttp.ContentTypeError, ValueError) as exc:
                raise MemoryClientError("Memory service returned invalid JSON") from exc
        if not isinstance(result, dict):
            raise MemoryClientError("Memory service response must be a JSON object")
        return result
