from __future__ import annotations

from dataclasses import replace

import pytest

from telefire.ai import MemoryDreamResult, MemoryDreamState, MemoryScopeState
from telefire.chat.identity import NamespacedIdentityCodec
from telefire.memory_admin import MemoryAdminService


class MemoryAdminStore:
    def __init__(self) -> None:
        self.scope = MemoryScopeState(scope_id="qq:group:694769138")
        self.dream = MemoryDreamState(
            scope_id="qq:group:694769138",
            last_attempt_at=10.0,
            last_success_at=11.0,
        )
        self.changes: list[tuple[str, bool, str | None]] = []

    async def set_dream_memory_enabled(
        self,
        scope_id: str,
        enabled: bool,
        display_name: str | None = None,
    ) -> None:
        self.changes.append((scope_id, enabled, display_name))
        self.scope = replace(
            self.scope,
            dream_enabled=enabled,
            display_name=display_name or self.scope.display_name,
        )

    async def get_memory_scope_state(self, scope_id: str) -> MemoryScopeState:
        assert scope_id == self.scope.scope_id
        return self.scope

    async def get_memory_dream_state(self, scope_id: str) -> MemoryDreamState:
        assert scope_id == self.dream.scope_id
        return self.dream


class DreamRunner:
    def __init__(self) -> None:
        self.calls = []

    async def run_backfill(self, chat_id, request):
        self.calls.append((chat_id, request))
        return MemoryDreamResult(
            messages_seen=500,
            messages_retained=472,
            documents_created=31,
            documents_unchanged=2,
        )


@pytest.fixture
def admin():
    store = MemoryAdminStore()
    runner = DreamRunner()
    service = MemoryAdminService(
        store=store,
        dream_runner=runner,
        identity_codec=NamespacedIdentityCodec("qq", "user", "group"),
        monotonic=iter((100.0, 112.5)).__next__,
    )
    return service, store, runner


@pytest.mark.asyncio
async def test_memory_admin_enables_dream_without_a_chat_transport(admin):
    service, store, _ = admin

    status = await service.set_dream(
        694769138,
        enabled=True,
        display_name="BetterGI v2",
    )

    assert store.changes == [
        ("qq:group:694769138", True, "BetterGI v2")
    ]
    assert status.scope_id == "qq:group:694769138"
    assert status.display_name == "BetterGI v2"
    assert status.dream_enabled is True
    assert status.dream_last_success_at == 11.0


@pytest.mark.asyncio
async def test_memory_admin_runs_existing_bounded_backfill(admin):
    service, _, runner = admin

    result = await service.backfill(
        694769138,
        mode="messages",
        value=500,
    )

    assert len(runner.calls) == 1
    chat_id, request = runner.calls[0]
    assert chat_id == 694769138
    assert request.mode == "messages"
    assert request.value == 500
    assert result.scope_id == "qq:group:694769138"
    assert result.requested == 500
    assert result.messages_seen == 500
    assert result.messages_retained == 472
    assert result.documents_created == 31
    assert result.documents_unchanged == 2
    assert result.elapsed_seconds == 12.5


@pytest.mark.asyncio
async def test_memory_admin_rejects_invalid_backfill_mode(admin):
    service, _, runner = admin

    with pytest.raises(ValueError, match="mode"):
        await service.backfill(694769138, mode="everything", value=500)

    assert runner.calls == []
