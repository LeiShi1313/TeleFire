from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace

import aiohttp
import pytest
from aiohttp.test_utils import TestServer

from telefire.onebot.ai import (
    QQ_IDENTITY_CODEC,
    OneBotChatTransport,
    OneBotHistorySource,
    OneBotMessageIdentityResolver,
    OneBotMessageMentionResolver,
    onebot_system_prompt,
)
from telefire.onebot.client import (
    OneBotActionError,
    OneBotReverseWebSocket,
)
from telefire.onebot.message import (
    OneBotMessage,
    OneBotMessageError,
)


class RecordingActionClient:
    def __init__(self, responses=()):
        self.calls = []
        self.responses = list(responses)

    async def call(self, action, params=None, *, timeout=None):
        self.calls.append((action, params or {}, timeout))
        if not self.responses:
            raise AssertionError(f"No response prepared for {action}")
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def group_event(
    *,
    message_id=101,
    sender_id=42,
    group_id=700,
    text="/ai hello",
    segments=None,
    post_type="message",
):
    return {
        "post_type": post_type,
        "message_type": "group",
        "self_id": 99,
        "user_id": sender_id,
        "group_id": group_id,
        "group_name": "Dog Food Filter",
        "message_id": message_id,
        "time": 1_700_000_000,
        "sender": {
            "user_id": sender_id,
            "nickname": "Alice",
            "card": "Alice Card",
            "role": "member",
        },
        "message": segments
        or [{"type": "text", "data": {"text": text}}],
        "raw_message": text,
    }


def private_event(
    *,
    message_id=201,
    sender_id=42,
    target_id=42,
    text="/ai hello",
    post_type="message",
):
    return {
        "post_type": post_type,
        "message_type": "private",
        "self_id": 99,
        "user_id": sender_id,
        "target_id": target_id,
        "message_id": message_id,
        "time": 1_700_000_000,
        "sender": {
            "user_id": sender_id,
            "nickname": "Cherry",
            "card": "",
        },
        "message": [{"type": "text", "data": {"text": text}}],
        "raw_message": text,
    }


def test_qq_identity_codec_separates_group_and_private_scopes():
    assert QQ_IDENTITY_CODEC.actor_id(42) == "qq:user:42"
    assert QQ_IDENTITY_CODEC.scope_id(700) == "qq:group:700"
    assert QQ_IDENTITY_CODEC.scope_id(-42) == "qq:private:42"
    assert QQ_IDENTITY_CODEC.parse_scope_id("qq:group:700") == 700
    assert QQ_IDENTITY_CODEC.parse_scope_id("qq:private:42") == -42
    assert QQ_IDENTITY_CODEC.message_source_id(-42, 9) == "qq:message:private:42:9"


def test_onebot_message_normalizes_reply_mentions_and_attachment_metadata():
    action_client = RecordingActionClient()
    payload = group_event(
        segments=[
            {"type": "reply", "data": {"id": "88"}},
            {"type": "text", "data": {"text": "/ai ask "}},
            {"type": "at", "data": {"qq": "123", "name": "Bob"}},
            {
                "type": "image",
                "data": {
                    "file": "photo.jpg",
                    "url": "https://example.test/photo.jpg",
                    "file_size": "321",
                    "summary": "[image]",
                },
            },
        ]
    )

    message = OneBotMessage.from_payload(payload, action_client=action_client)

    assert message.id == 101
    assert message.chat_id == 700
    assert message.sender_id == 42
    assert message.reply_to_msg_id == 88
    assert message.raw_text == "/ai ask @Bob"
    assert message.date == datetime.fromtimestamp(1_700_000_000, UTC)
    assert message.out is False
    assert message.file is not None
    assert message.file.name == "photo.jpg"
    assert message.file.size == 321
    assert not hasattr(message.file, "data")


@pytest.mark.asyncio
async def test_onebot_attachment_bytes_are_fetched_on_demand_only():
    action_client = RecordingActionClient(
        responses=[{"base64": "aGVsbG8="}]
    )
    message = OneBotMessage.from_payload(
        group_event(
            segments=[
                {
                    "type": "image",
                    "data": {"file": "opaque-image-token"},
                }
            ]
        ),
        action_client=action_client,
    )

    assert message.file is not None
    assert await message.download_media(file=bytes) == b"hello"
    assert action_client.calls[0][0] == "get_file"
    assert not hasattr(message.file, "data")


def test_onebot_private_self_message_uses_target_as_conversation_scope():
    message = OneBotMessage.from_payload(
        private_event(
            sender_id=99,
            target_id=42,
            post_type="message_sent",
        ),
        action_client=RecordingActionClient(),
    )

    assert message.chat_id == -42
    assert message.sender_id == 99
    assert message.out is True


def test_onebot_private_history_uses_explicit_peer_when_target_is_absent():
    payload = private_event(
        sender_id=99,
        target_id=42,
        post_type="message_sent",
    )
    payload.pop("target_id")

    message = OneBotMessage.from_payload(
        payload,
        action_client=RecordingActionClient(),
        private_peer_id=42,
    )

    assert message.chat_id == -42


@pytest.mark.parametrize(
    "patch",
    [
        {"message_id": "not-a-number"},
        {"message": "CQ string is not accepted"},
        {"message_type": "guild"},
        {"group_id": None},
    ],
)
def test_onebot_message_rejects_malformed_external_events(patch):
    payload = group_event()
    payload.update(patch)

    with pytest.raises(OneBotMessageError):
        OneBotMessage.from_payload(
            payload,
            action_client=RecordingActionClient(),
        )


@pytest.mark.asyncio
async def test_onebot_transport_replaces_placeholder_with_one_final_reply():
    action_client = RecordingActionClient(
        responses=[
            {"message_id": 501},
            {"message_id": 502},
            None,
        ]
    )
    trigger = OneBotMessage.from_payload(
        group_event(),
        action_client=action_client,
    )
    transport = OneBotChatTransport(action_client)

    sent = await transport.reply(trigger, "Thinking...", presentation="plain")
    streamed = await transport.update(
        sent,
        "partial",
        presentation="agent",
        wait=False,
    )
    finalized = await transport.update(
        sent,
        "final",
        presentation="agent",
        wait=True,
    )

    assert streamed is False
    assert finalized is True
    assert sent.id == 502
    assert sent.text == "final"
    assert [call[0] for call in action_client.calls] == [
        "send_group_msg",
        "send_group_msg",
        "delete_msg",
    ]
    assert action_client.calls[0][1]["message"][0] == {
        "type": "reply",
        "data": {"id": "101"},
    }
    assert action_client.calls[1][1]["message"][1] == {
        "type": "text",
        "data": {"text": "final"},
    }


@pytest.mark.asyncio
async def test_onebot_transport_fetches_reply_and_deletes_by_action():
    action_client = RecordingActionClient(
        responses=[
            group_event(message_id=88, text="parent"),
            None,
        ]
    )
    message = OneBotMessage.from_payload(
        group_event(
            segments=[
                {"type": "reply", "data": {"id": "88"}},
                {"type": "text", "data": {"text": "/ai follow up"}},
            ]
        ),
        action_client=action_client,
    )
    transport = OneBotChatTransport(action_client)

    parent = await transport.get_reply(message)
    await transport.delete(message)

    assert parent is not None
    assert parent.id == 88
    assert [call[0] for call in action_client.calls] == ["get_msg", "delete_msg"]


@pytest.mark.asyncio
async def test_private_reply_lookup_supplies_conversation_peer_to_get_msg():
    parent = private_event(
        message_id=88,
        sender_id=99,
        target_id=42,
        text="parent",
        post_type="message_sent",
    )
    parent.pop("target_id")
    action_client = RecordingActionClient(responses=[parent])
    message = OneBotMessage.from_payload(
        {
            **private_event(
                sender_id=42,
                target_id=42,
                text="/ai follow up",
            ),
            "message": [
                {"type": "reply", "data": {"id": "88"}},
                {"type": "text", "data": {"text": "/ai follow up"}},
            ],
        },
        action_client=action_client,
    )

    fetched = await OneBotChatTransport(action_client).get_reply(message)

    assert fetched is not None
    assert fetched.chat_id == -42


@pytest.mark.asyncio
async def test_onebot_history_uses_transport_order_not_random_message_id_order():
    action_client = RecordingActionClient(
        responses=[
            {
                "messages": [
                    group_event(
                        message_id=2_000_000_000,
                        text="older",
                    ),
                    group_event(
                        message_id=3,
                        text="newer",
                    ),
                    group_event(
                        message_id=100,
                        text="/ai2 summarize",
                    ),
                ]
            }
        ]
    )
    trigger = OneBotMessage.from_payload(
        group_event(message_id=100, text="/ai2 summarize"),
        action_client=action_client,
    )

    messages = await OneBotHistorySource(action_client).fetch_recent(
        trigger,
        limit=2,
    )

    assert [message.id for message in messages] == [2_000_000_000, 3]
    action, params, _ = action_client.calls[0]
    assert action == "get_group_msg_history"
    assert params["group_id"] == "700"
    assert params["message_seq"] == "100"
    assert params["reverse_order"] is True


@pytest.mark.asyncio
async def test_onebot_history_resumes_from_latest_window_when_cursor_is_gone():
    action_client = RecordingActionClient(
        responses=[
            OneBotActionError("get_msg", 1404, "message not found"),
            {
                "messages": [
                    group_event(message_id=501, text="available one"),
                    group_event(message_id=502, text="available two"),
                ]
            },
        ]
    )

    messages = await OneBotHistorySource(action_client).fetch_after(
        700,
        after_message_id=400,
        until=datetime.max.replace(tzinfo=UTC),
        limit=2,
    )

    assert [message.id for message in messages] == [501, 502]


@pytest.mark.asyncio
async def test_onebot_identity_and_mentions_use_display_labels_when_available():
    message = OneBotMessage.from_payload(
        group_event(
            segments=[
                {"type": "text", "data": {"text": "hello "}},
                {"type": "at", "data": {"qq": "123", "name": "Bob"}},
            ]
        ),
        action_client=RecordingActionClient(),
    )

    identity = await OneBotMessageIdentityResolver().resolve(message)
    mentions = await OneBotMessageMentionResolver().resolve(message)

    assert identity.subject_id == "qq:user:42"
    assert identity.subject_display_name == "Alice Card"
    assert identity.scope_display_name == "Dog Food Filter"
    assert [(item.user_id, item.display_name) for item in mentions] == [(123, "Bob")]


def test_onebot_prompt_requests_plain_qq_output():
    prompt = onebot_system_prompt("Base policy.")

    assert prompt.startswith("Base policy.")
    assert "QQ plain text" in prompt
    assert "Markdown" in prompt


@pytest.mark.asyncio
async def test_reverse_websocket_authenticates_and_correlates_actions():
    seen = []
    received = asyncio.Event()

    async def on_event(payload):
        seen.append(payload)
        received.set()

    bridge = OneBotReverseWebSocket(
        token="secret",
        self_id=99,
        event_handler=on_event,
    )
    async with TestServer(bridge.application) as server:
        async with aiohttp.ClientSession() as session:
            with pytest.raises(aiohttp.WSServerHandshakeError) as rejected:
                await session.ws_connect(
                    server.make_url("/onebot"),
                    headers={"Authorization": "Bearer wrong", "X-Self-ID": "99"},
                )
            assert rejected.value.status == 401

            websocket = await session.ws_connect(
                server.make_url("/onebot"),
                headers={
                    "Authorization": "Bearer secret",
                    "X-Self-ID": "99",
                },
            )
            await websocket.send_json(group_event())
            await asyncio.wait_for(received.wait(), timeout=1)

            pending = asyncio.create_task(
                bridge.call("get_status", {}, timeout=1)
            )
            action = await websocket.receive_json(timeout=1)
            await websocket.send_json(
                {
                    "status": "ok",
                    "retcode": 0,
                    "data": {"online": True},
                    "echo": action["echo"],
                }
            )

            assert await pending == {"online": True}
            assert seen[0]["message_id"] == 101
            await websocket.close()
    await bridge.close()


@pytest.mark.asyncio
async def test_reverse_websocket_dispatches_events_concurrently():
    first_started = asyncio.Event()
    second_seen = asyncio.Event()
    release_first = asyncio.Event()

    async def on_event(payload):
        if payload["message_id"] == 101:
            first_started.set()
            await release_first.wait()
        else:
            second_seen.set()

    bridge = OneBotReverseWebSocket(
        token="secret",
        self_id=99,
        event_handler=on_event,
        event_concurrency=2,
    )
    async with TestServer(bridge.application) as server:
        async with aiohttp.ClientSession() as session:
            websocket = await session.ws_connect(
                server.make_url("/onebot"),
                headers={
                    "Authorization": "Bearer secret",
                    "X-Self-ID": "99",
                },
            )
            await websocket.send_json(group_event(message_id=101))
            await asyncio.wait_for(first_started.wait(), timeout=1)
            await websocket.send_json(group_event(message_id=102))
            await asyncio.wait_for(second_seen.wait(), timeout=1)
            release_first.set()
            await websocket.close()
    await bridge.close()


@pytest.mark.asyncio
async def test_reverse_websocket_surfaces_action_failures_without_payload_leaks():
    bridge = OneBotReverseWebSocket(token="secret", self_id=99)
    async with TestServer(bridge.application) as server:
        async with aiohttp.ClientSession() as session:
            websocket = await session.ws_connect(
                server.make_url("/onebot"),
                headers={
                    "Authorization": "Bearer secret",
                    "X-Self-ID": "99",
                },
            )
            pending = asyncio.create_task(
                bridge.call("get_msg", {"message_id": "1"}, timeout=1)
            )
            action = await websocket.receive_json(timeout=1)
            await websocket.send_json(
                {
                    "status": "failed",
                    "retcode": 1404,
                    "message": "message not found",
                    "data": {"private": "must not be copied into the error"},
                    "echo": action["echo"],
                }
            )

            with pytest.raises(OneBotActionError, match="message not found") as exc:
                await pending
            assert "private" not in str(exc.value)
            await websocket.close()
    await bridge.close()
