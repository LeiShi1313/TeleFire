from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

import aiohttp


@dataclass(frozen=True, slots=True)
class MemoryIngestResult:
    created: bool
    facts_added: int
    episodes_added: int


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
    ) -> MemoryIngestResult: ...

    async def upsert_identities(self, identities: dict[str, str]) -> int: ...

    async def revise(
        self,
        *,
        subject_id: str,
        instruction: str,
        evidence: str | None,
        scope_id: str | None,
    ) -> dict[str, int | bool]: ...


class MemoryClientError(RuntimeError):
    pass


class HTTPMemoryClient:
    def __init__(self, base_url: str, *, timeout: float = 10.0):
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
    ) -> MemoryIngestResult:
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
        created = payload.get("created")
        facts_added = payload.get("facts_added")
        episodes_added = payload.get("episodes_added")
        if (
            not isinstance(created, bool)
            or isinstance(facts_added, bool)
            or not isinstance(facts_added, int)
            or facts_added < 0
            or isinstance(episodes_added, bool)
            or not isinstance(episodes_added, int)
            or episodes_added < 0
        ):
            raise MemoryClientError("Memory ingest response is malformed")
        return MemoryIngestResult(
            created=created,
            facts_added=facts_added,
            episodes_added=episodes_added,
        )

    async def upsert_identities(self, identities: dict[str, str]) -> int:
        if not identities:
            return 0
        payload = await self._post(
            "/v1/memory/identities",
            {
                "items": [
                    {"key": key, "display_name": identities[key]}
                    for key in sorted(identities)
                ]
            },
        )
        updated = payload.get("updated")
        if isinstance(updated, bool) or not isinstance(updated, int) or updated < 0:
            raise MemoryClientError("Memory identity response is malformed")
        return updated

    async def revise(
        self,
        *,
        subject_id: str,
        instruction: str,
        evidence: str | None,
        scope_id: str | None,
    ) -> dict[str, int | bool]:
        payload = await self._post(
            "/v1/memory/revise",
            {
                "subject_id": subject_id,
                "instruction": instruction,
                "evidence": evidence,
                "scope_id": scope_id,
            },
        )
        profile_updated = payload.get("profile_updated")
        suppressed_count = payload.get("suppressed_count")
        if (
            not isinstance(profile_updated, bool)
            or isinstance(suppressed_count, bool)
            or not isinstance(suppressed_count, int)
            or suppressed_count < 0
        ):
            raise MemoryClientError("Memory revision response is malformed")
        return {
            "profile_updated": profile_updated,
            "suppressed_count": suppressed_count,
        }

    async def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        if self._session is None:
            self._session = aiohttp.ClientSession(timeout=self._timeout)
        async with self._session.post(
            f"{self._base_url}{path}", json=payload
        ) as response:
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
