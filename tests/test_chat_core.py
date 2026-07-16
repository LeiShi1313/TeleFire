from __future__ import annotations

import ast
from collections.abc import AsyncIterator
from dataclasses import fields
from datetime import UTC, datetime
from pathlib import Path
import sqlite3

import pytest

from telefire.ai import (
    AIConversationHandler,
    AIResponder,
    AIStateRepository,
    AgentEvent,
    AgentRunRequest,
    MemoryScopeState,
    PromptBuilder,
)
from telefire.chat.attachments import AttachmentDescription, AttachmentReference
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
    assert telegram.parse_scope_id("telegram:chat:7") == 7
    assert qq.parse_scope_id("qq:group:7") == 7
    assert qq.parse_scope_id("telegram:chat:7") is None


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
    def __init__(self):
        self.requests: list[AgentRunRequest] = []

    async def run(self, request: AgentRunRequest) -> AsyncIterator[AgentEvent]:
        self.requests.append(request)
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
        self.replies: list[tuple[object, str, ChatPresentation]] = []
        self.reply_targets: dict[object, object] = {}
        self.deleted: list[object] = []

    async def get_reply(self, message):
        return self.reply_targets.get(message)

    async def reply(self, message, text, *, presentation):
        self.replies.append((message, text, presentation))
        self.sent.text = text
        return self.sent

    async def update(self, message, text, *, presentation, wait):
        self.updates.append((text, presentation, wait))
        self.sent.text = text
        return True

    async def delete(self, message):
        self.deleted.append(message)

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


class MinimalMessage:
    def __init__(
        self,
        text: str,
        *,
        message_id: int = 1,
        chat_id: int = 7,
        sender_id: int = 42,
        reply_to_message_id: int | None = None,
    ):
        self.id = message_id
        self.chat_id = chat_id
        self.sender_id = sender_id
        self.raw_text = text
        self.reply_to_msg_id = reply_to_message_id
        self.date = None


class FakeStore:
    def __init__(self):
        self.saved = []

    async def get_answer(self, scope_id, answer_message_id):
        return None

    async def get_turn_for_message(self, scope_id, message_id):
        return None

    async def save_answer(self, marker):
        self.saved.append(marker)

    async def is_allowed(self, actor_id):
        return False

    async def get_last_request_at(self, actor_id):
        return None

    async def set_last_request_at(self, actor_id, timestamp):
        return None

    async def allow_user(self, actor_id):
        return None

    async def deny_user(self, actor_id):
        return None

    async def mark_memory_excluded_message(self, scope_id, message_id, kind):
        return None

    async def get_memory_scope_state(self, scope_id):
        return MemoryScopeState(
            scope_id=scope_id,
            continuous_enabled=True,
        )


@pytest.mark.asyncio
async def test_handler_uses_transport_for_sdk_operations():
    transport = FakeTransport()
    gateway = FakeGateway()
    handler = AIConversationHandler(
        owner_id=42,
        responder=AIResponder(gateway, transport=transport),
        store=FakeStore(),
        prompt_builder=PromptBuilder(transport=transport),
        transport=transport,
    )
    message = MinimalMessage("/ai question")

    handled = await handler.handle(message)

    assert handled is True
    assert transport.replies[0] == (message, "Thinking...", "plain")
    assert transport.updates[-1] == ("final", "agent", True)


class OpaqueAttachmentDescriber:
    def has_attachment(self, message):
        return True

    async def describe(self, message):
        return AttachmentDescription(
            context_text="Generated image description",
            memory_text="The subject shared an image.",
        )


@pytest.mark.asyncio
async def test_attachment_detection_does_not_require_telegram_file_attributes():
    transport = FakeTransport()
    gateway = FakeGateway()
    handler = AIConversationHandler(
        owner_id=42,
        responder=AIResponder(gateway, transport=transport),
        store=FakeStore(),
        prompt_builder=PromptBuilder(
            transport=transport,
            attachment_describer=OpaqueAttachmentDescriber(),
        ),
        transport=transport,
    )
    message = MinimalMessage("/ai")

    handled = await handler.handle(message)

    assert handled is True
    assert transport.updates[-1] == ("final", "agent", True)


@pytest.mark.asyncio
async def test_recent_history_does_not_assume_transport_message_ids_are_ordered():
    class History:
        async def fetch_recent(self, trigger, *, limit):
            messages = (
                MinimalMessage(
                    "first",
                    message_id=2_000_000_000,
                    chat_id=trigger.chat_id,
                    sender_id=10,
                ),
                MinimalMessage(
                    "second",
                    message_id=3,
                    chat_id=trigger.chat_id,
                    sender_id=20,
                ),
            )
            occurred_at = datetime(2026, 7, 16, 12, tzinfo=UTC)
            for message in messages:
                message.date = occurred_at
            return messages

    builder = PromptBuilder(
        history_source=History(),
        transport=FakeTransport(),
    )
    trigger = MinimalMessage(
        "/ai2 summarize",
        message_id=100,
        chat_id=7,
        sender_id=42,
    )

    context = await builder.load_chat_context(trigger, recent_messages=2)

    assert [message.content for message in context.messages] == ["first", "second"]


def test_shared_ai_module_has_no_telegram_adapter_imports():
    import telefire.ai as ai_module

    tree = ast.parse(Path(ai_module.__file__).read_text())
    imported = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    imported.update(
        node.module or ""
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
    )

    assert not any(
        name == "telethon"
        or name.startswith("telethon.")
        or name == "telefire.telegram"
        or name.startswith("telefire.telegram.")
        for name in imported
    )


@pytest.mark.asyncio
async def test_memory_coordinates_follow_the_injected_chat_identity_codec():
    transport = FakeTransport()
    gateway = FakeGateway()
    qq = NamespacedIdentityCodec(
        source="qq",
        actor_kind="user",
        scope_kind="group",
    )
    prompt_builder = PromptBuilder(
        transport=transport,
        identity_codec=qq,
    )
    store = FakeStore()
    handler = AIConversationHandler(
        owner_id=42,
        responder=AIResponder(gateway, transport=transport),
        store=store,
        prompt_builder=prompt_builder,
        transport=transport,
        memory=object(),
        identity_codec=qq,
    )
    message = MinimalMessage("/ai who am I?")

    assert await handler.handle(message) is True

    memory = gateway.requests[0].memory
    assert memory is not None
    assert memory.scope_id == "qq:group:7"
    assert [anchor.identity for anchor in memory.anchors] == ["qq:user:42"]
    assert store.saved[0].scope_id == "qq:group:7"
    assert store.saved[0].requester_id == "qq:user:42"


@pytest.mark.asyncio
async def test_state_repository_migrates_legacy_telegram_identity_columns(tmp_path):
    path = tmp_path / "ai.db"
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE ai_answers (
            chat_id INTEGER NOT NULL,
            answer_message_id INTEGER NOT NULL,
            trigger_message_id INTEGER NOT NULL,
            requester_id INTEGER NOT NULL,
            prompt TEXT NOT NULL,
            answer_text TEXT NOT NULL,
            parent_answer_message_id INTEGER,
            reference_context TEXT NOT NULL,
            agent_session_id TEXT,
            agent_entry_id TEXT,
            PRIMARY KEY (chat_id, answer_message_id)
        );
        INSERT INTO ai_answers VALUES (
            -1001, 100, 1, 20, 'question', 'answer', NULL, '', 's1', 'e1'
        );
        CREATE TABLE ai_whitelist (
            user_id INTEGER PRIMARY KEY,
            allowed_at REAL NOT NULL
        );
        INSERT INTO ai_whitelist VALUES (20, 1);
        CREATE TABLE ai_usage (
            user_id INTEGER PRIMARY KEY,
            last_request_at REAL NOT NULL
        );
        INSERT INTO ai_usage VALUES (20, 2);
        CREATE TABLE ai_memory_excluded_messages (
            chat_id INTEGER NOT NULL,
            message_id INTEGER NOT NULL,
            kind TEXT NOT NULL,
            created_at REAL NOT NULL,
            PRIMARY KEY (chat_id, message_id)
        );
        INSERT INTO ai_memory_excluded_messages VALUES (-1001, 101, 'ai-answer', 3);
        """
    )
    connection.commit()
    connection.close()

    store = await AIStateRepository(path).connect()
    try:
        marker = await store.get_answer("telegram:chat:-1001", 100)

        assert marker is not None
        assert marker.scope_id == "telegram:chat:-1001"
        assert marker.requester_id == "telegram:user:20"
        assert await store.is_allowed("telegram:user:20") is True
        assert await store.is_allowed("qq:user:20") is False
        assert await store.get_last_request_at("telegram:user:20") == 2
        assert await store.is_memory_excluded_message(
            "telegram:chat:-1001",
            101,
        )
    finally:
        await store.close()
