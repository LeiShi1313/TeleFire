from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import fields

import pytest

from telefire.ai import (
    AIResponder,
    AgentEvent,
    AgentRunRequest,
)
from telefire.chat.attachments import AttachmentReference
from telefire.chat.commands import (
    AIAskCommand,
    AICancelCommand,
    AccessCommand,
    InvalidCommand,
    MemoryBackfillCommand,
    MemoryModeCommand,
    MemoryRememberCommand,
    MemoryStatusCommand,
    parse_chat_command,
)
from telefire.chat.identity import NamespacedIdentityCodec
from telefire.chat.transport import ChatPresentation


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("/ai hello", AIAskCommand(prompt="hello")),
        ("/ai10 hello", AIAskCommand(prompt="hello", recent_messages=10)),
        (
            "/ai10@TelefireBot summarize",
            AIAskCommand(prompt="summarize", recent_messages=10),
        ),
        ("/ai_cancel", AICancelCommand()),
        ("/ai_allow", AccessCommand(allowed=True)),
        ("/ai_deny", AccessCommand(allowed=False)),
        (
            "/ai_memory remember this",
            MemoryRememberCommand(instruction="remember this"),
        ),
        (
            "/ai_memory_backfill messages 500",
            MemoryBackfillCommand(mode="messages", value=500),
        ),
        (
            "/ai_memory_enable qq-group-alias",
            MemoryModeCommand(
                mode="continuous",
                enabled=True,
                target="qq-group-alias",
            ),
        ),
        ("/ai_memory_status", MemoryStatusCommand()),
        (
            "/ai_memory_backfill days 31",
            InvalidCommand(name="/ai_memory_backfill"),
        ),
    ],
)
def test_chat_commands_are_parsed_without_transport_assumptions(text, expected):
    assert parse_chat_command(text) == expected


def test_identity_codec_keeps_network_identities_disjoint():
    telegram = NamespacedIdentityCodec(
        source="telegram",
        actor_kind="user",
        scope_kind="chat",
    )
    qq = NamespacedIdentityCodec(
        source="qq",
        actor_kind="user",
        scope_kind="group",
    )

    assert telegram.actor_id(42) == "telegram:user:42"
    assert qq.actor_id(42) == "qq:user:42"
    assert telegram.scope_id(7) == "telegram:chat:7"
    assert qq.scope_id(7) == "qq:group:7"
    assert qq.message_source_id(7, 9) == "qq:message:7:9"
    assert qq.thread_document_id(7, 9) == "qq:thread:7:9"
    assert qq.revision_document_id(7, 9) == "qq:revision:7:9"


def test_attachment_reference_contains_metadata_but_no_binary_payload():
    reference = AttachmentReference(
        key="onebot11:self-1:message-2:image-0",
        kind="image",
        mime_type="image/jpeg",
        filename="photo.jpg",
        size_bytes=1234,
    )

    assert reference.size_bytes == 1234
    assert "data" not in {item.name for item in fields(reference)}
    assert "content" not in {item.name for item in fields(reference)}
    assert not any(
        isinstance(getattr(reference, item.name), bytes) for item in fields(reference)
    )


class FakeGateway:
    async def run(self, request: AgentRunRequest) -> AsyncIterator[AgentEvent]:
        yield AgentEvent(
            type="run_started",
            run_id=request.run_id,
            session_id="session-1",
        )
        yield AgentEvent(type="text_delta", delta="partial", reset=True)
        yield AgentEvent(
            type="run_completed",
            session_id="session-1",
            entry_id="entry-1",
            answer="final",
        )

    async def cancel(self, run_id: str) -> bool:
        return True


class FakeSentMessage:
    def __init__(self):
        self.id = 100
        self.text = "Thinking..."


class FakeTransport:
    def __init__(self):
        self.sent = FakeSentMessage()
        self.updates: list[tuple[str, ChatPresentation, bool]] = []

    async def get_reply(self, message):
        return None

    async def reply(self, message, text, *, presentation):
        assert presentation == "plain"
        self.sent.text = text
        return self.sent

    async def update(self, message, text, *, presentation, wait):
        self.updates.append((text, presentation, wait))
        self.sent.text = text
        return True

    async def delete(self, message):
        return None

    def is_outgoing(self, message):
        return False


@pytest.mark.asyncio
async def test_responder_streams_through_transport_not_message_sdk_methods():
    transport = FakeTransport()
    responder = AIResponder(FakeGateway(), transport=transport)
    trigger = object()
    request = AgentRunRequest(
        run_id="run-1",
        session_id=None,
        parent_entry_id=None,
        prompt="question",
        context=(),
        system_prompt="system",
        tool_policy="owner",
    )

    result = await responder.answer(trigger, request)

    assert result.succeeded is True
    assert result.message is transport.sent
    assert transport.updates[-1] == ("final", "agent", True)
