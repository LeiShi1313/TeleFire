from __future__ import annotations

import asyncio

import aiohttp
import pytest
from aiohttp.test_utils import TestServer

from telefire.ai import MemoryDreamResult, MemoryDreamState, MemoryScopeState
from telefire.chat.identity import NamespacedIdentityCodec
from telefire.memory_admin import MemoryAdminService
from telefire.onebot.client import OneBotReverseWebSocket
from telefire.onebot.memory_admin import (
    OneBotMemoryAdminClient,
    mount_onebot_memory_admin,
)
from telefire.plugins.base import command_registry
import telefire.plugins.onebot_ai as onebot_plugin


class Store:
    def __init__(self) -> None:
        self.scope = MemoryScopeState(scope_id="qq:group:694769138")

    async def set_dream_memory_enabled(
        self,
        scope_id: str,
        enabled: bool,
        display_name: str | None = None,
    ) -> None:
        self.scope = MemoryScopeState(
            scope_id=scope_id,
            display_name=display_name,
            dream_enabled=enabled,
        )

    async def get_memory_scope_state(self, scope_id: str) -> MemoryScopeState:
        assert scope_id == self.scope.scope_id
        return self.scope

    async def get_memory_dream_state(self, scope_id: str) -> MemoryDreamState:
        return MemoryDreamState(scope_id=scope_id)


class Runner:
    async def run_backfill(self, chat_id, request):
        assert chat_id == 694769138
        assert request.mode == "messages"
        assert request.value == 500
        return MemoryDreamResult(500, 480, 20, 1)


@pytest.fixture
def bridge():
    bridge = OneBotReverseWebSocket(token="secret", self_id=715293274)
    mount_onebot_memory_admin(
        bridge,
        MemoryAdminService(
            store=Store(),
            dream_runner=Runner(),
            identity_codec=NamespacedIdentityCodec("qq", "user", "group"),
        ),
        display_name_resolver=lambda group_id: (
            "BetterGI v2" if group_id == 694769138 else None
        ),
    )
    return bridge


@pytest.mark.asyncio
async def test_onebot_memory_admin_requires_adapter_authentication(bridge):
    async with TestServer(bridge.application) as server:
        async with aiohttp.ClientSession() as session:
            response = await session.get(
                server.make_url("/admin/memory/status?group_id=694769138")
            )

            assert response.status == 401


@pytest.mark.asyncio
async def test_onebot_memory_admin_enables_dream_and_backfills(bridge):
    headers = {
        "Authorization": "Bearer secret",
        "X-Self-ID": "715293274",
    }
    async with TestServer(bridge.application) as server:
        async with aiohttp.ClientSession(headers=headers) as session:
            enabled = await session.post(
                server.make_url("/admin/memory/dream"),
                json={"group_id": 694769138, "enabled": True},
            )
            backfilled = await session.post(
                server.make_url("/admin/memory/backfill"),
                json={
                    "group_id": 694769138,
                    "mode": "messages",
                    "value": 500,
                },
            )

            assert enabled.status == 200
            assert await enabled.json() == {
                "continuous_enabled": False,
                "continuous_cursor_message_id": None,
                "continuous_last_attempt_at": None,
                "continuous_last_error": None,
                "continuous_last_success_at": None,
                "display_name": "BetterGI v2",
                "dream_enabled": True,
                "dream_last_attempt_at": None,
                "dream_last_error": None,
                "dream_last_success_at": None,
                "scope_id": "qq:group:694769138",
            }
            payload = await backfilled.json()
            assert backfilled.status == 200
            assert payload["messages_seen"] == 500
            assert payload["messages_retained"] == 480
            assert payload["documents_created"] == 20
            assert payload["requested"] == 500
            assert payload["scope_id"] == "qq:group:694769138"


@pytest.mark.asyncio
async def test_onebot_memory_admin_rejects_private_chat_ids(bridge):
    headers = {
        "Authorization": "Bearer secret",
        "X-Self-ID": "715293274",
    }
    async with TestServer(bridge.application) as server:
        async with aiohttp.ClientSession(headers=headers) as session:
            response = await session.post(
                server.make_url("/admin/memory/dream"),
                json={"group_id": -715293274, "enabled": True},
            )

            assert response.status == 400
            assert "positive" in (await response.json())["error"]


@pytest.mark.asyncio
async def test_onebot_memory_admin_client_round_trip(bridge):
    async with TestServer(bridge.application) as server:
        client = OneBotMemoryAdminClient(
            str(server.make_url("/")),
            token="secret",
            self_id=715293274,
        )

        result = await asyncio.to_thread(
            client.set_dream,
            694769138,
            enabled=True,
        )

        assert result["scope_id"] == "qq:group:694769138"
        assert result["display_name"] == "BetterGI v2"
        assert result["dream_enabled"] is True


def test_onebot_quiet_memory_commands_are_registered():
    commands = command_registry.as_fire_commands()["onebot"]["memory"]

    assert set(commands) == {
        "backfill",
        "dream-disable",
        "dream-enable",
        "status",
    }


def test_onebot_quiet_dream_enable_prints_admin_result(monkeypatch, capsys):
    class Client:
        def set_dream(self, group_id, *, enabled, display_name):
            assert group_id == 694769138
            assert enabled is True
            assert display_name == "BetterGI v2"
            return {
                "scope_id": "qq:group:694769138",
                "display_name": display_name,
                "dream_enabled": True,
            }

    monkeypatch.setattr(
        onebot_plugin,
        "_onebot_memory_admin_client",
        lambda **_kwargs: Client(),
    )

    onebot_plugin.OneBotMemoryDreamEnable()(694769138, "BetterGI v2")

    output = capsys.readouterr().out
    assert '"scope_id": "qq:group:694769138"' in output
    assert '"display_name": "BetterGI v2"' in output


def test_onebot_memory_cli_uses_configured_publish_address(monkeypatch):
    captured = {}

    class Client:
        def __init__(self, base_url, **kwargs):
            captured["base_url"] = base_url
            captured.update(kwargs)

    monkeypatch.setenv("TELEFIRE_ONEBOT_TOKEN", "secret")
    monkeypatch.setenv("TELEFIRE_ONEBOT_SELF_ID", "329787230")
    monkeypatch.setenv("TELEFIRE_ONEBOT_PUBLISH_HOST", "100.99.247.60")
    monkeypatch.setenv("TELEFIRE_ONEBOT_PUBLISH_PORT", "18867")
    monkeypatch.delenv("TELEFIRE_ONEBOT_ADMIN_URL", raising=False)
    monkeypatch.setattr(onebot_plugin, "OneBotMemoryAdminClient", Client)

    onebot_plugin._onebot_memory_admin_client()

    assert captured == {
        "base_url": "http://100.99.247.60:18867",
        "token": "secret",
        "self_id": 329787230,
        "timeout": 900,
    }
