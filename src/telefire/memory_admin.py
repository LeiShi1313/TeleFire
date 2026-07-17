from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable, Literal, Protocol, cast

from telefire.ai import (
    MemoryDreamResult,
    MemoryDreamRunner,
    MemoryDreamState,
    MemoryScopeState,
)
from telefire.chat.commands import MemoryBackfillCommand
from telefire.chat.identity import IdentityCodec


class MemoryAdminStore(Protocol):
    async def set_dream_memory_enabled(
        self,
        scope_id: str,
        enabled: bool,
        display_name: str | None = None,
    ) -> None: ...

    async def get_memory_scope_state(self, scope_id: str) -> MemoryScopeState: ...

    async def get_memory_dream_state(self, scope_id: str) -> MemoryDreamState: ...


@dataclass(frozen=True, slots=True)
class MemoryAdminStatus:
    scope_id: str
    display_name: str | None
    continuous_enabled: bool
    dream_enabled: bool
    continuous_cursor_message_id: int | None
    continuous_last_attempt_at: float | None
    continuous_last_success_at: float | None
    continuous_last_error: str | None
    dream_last_attempt_at: float | None
    dream_last_success_at: float | None
    dream_last_error: str | None


@dataclass(frozen=True, slots=True)
class MemoryAdminBackfillResult:
    scope_id: str
    mode: Literal["days", "messages"]
    requested: int
    messages_seen: int
    messages_retained: int
    documents_created: int
    documents_unchanged: int
    elapsed_seconds: float


class MemoryAdminService:
    """Transport-free administrative operations for a running memory adapter."""

    def __init__(
        self,
        *,
        store: MemoryAdminStore,
        dream_runner: MemoryDreamRunner,
        identity_codec: IdentityCodec,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._store = store
        self._dream_runner = dream_runner
        self._identity_codec = identity_codec
        self._monotonic = monotonic

    async def set_dream(
        self,
        chat_id: int,
        *,
        enabled: bool,
        display_name: str | None = None,
    ) -> MemoryAdminStatus:
        scope_id = self._identity_codec.scope_id(chat_id)
        await self._store.set_dream_memory_enabled(
            scope_id,
            enabled,
            display_name,
        )
        return await self.status(chat_id)

    async def status(self, chat_id: int) -> MemoryAdminStatus:
        scope_id = self._identity_codec.scope_id(chat_id)
        scope = await self._store.get_memory_scope_state(scope_id)
        dream = await self._store.get_memory_dream_state(scope_id)
        return MemoryAdminStatus(
            scope_id=scope_id,
            display_name=scope.display_name,
            continuous_enabled=scope.continuous_enabled,
            dream_enabled=scope.dream_enabled,
            continuous_cursor_message_id=scope.continuous_cursor_message_id,
            continuous_last_attempt_at=scope.continuous_last_attempt_at,
            continuous_last_success_at=scope.continuous_last_success_at,
            continuous_last_error=scope.continuous_last_error,
            dream_last_attempt_at=dream.last_attempt_at,
            dream_last_success_at=dream.last_success_at,
            dream_last_error=dream.last_error,
        )

    async def backfill(
        self,
        chat_id: int,
        *,
        mode: str,
        value: int,
    ) -> MemoryAdminBackfillResult:
        if mode not in {"days", "messages"}:
            raise ValueError("Memory backfill mode must be 'days' or 'messages'")
        bounded_mode = cast(Literal["days", "messages"], mode)
        request = MemoryBackfillCommand(mode=bounded_mode, value=value)
        started_at = self._monotonic()
        result: MemoryDreamResult = await self._dream_runner.run_backfill(
            chat_id,
            request,
        )
        elapsed_seconds = max(0.0, self._monotonic() - started_at)
        return MemoryAdminBackfillResult(
            scope_id=self._identity_codec.scope_id(chat_id),
            mode=bounded_mode,
            requested=value,
            messages_seen=result.messages_seen,
            messages_retained=result.messages_retained,
            documents_created=result.documents_created,
            documents_unchanged=result.documents_unchanged,
            elapsed_seconds=round(elapsed_seconds, 3),
        )
