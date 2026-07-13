from collections.abc import AsyncIterator

import pytest
from telethon.errors import FloodWaitError
from telethon.tl import functions as telegram_functions
from telethon.tl import types as telegram_types

from telefire.ai import (
    AIConversationHandler,
    AIResponder,
    AISettings,
    AgentEvent,
    AgentRunRequest,
    MemoryBackfillRequest,
    PromptBuilder,
    parse_ai_trigger,
    parse_memory_backfill,
    parse_memory_revision,
)
from telefire.plugins.base import command_registry
import telefire.plugins.ai  # noqa: F401


class FakeAnswer:
    def __init__(self, text: str):
        self.id = 100
        self.text = text
        self.edits: list[str] = []
        self.edit_calls: list[tuple[str, dict]] = []

    async def edit(self, text: str, **kwargs):
        self.text = text
        self.edits.append(text)
        self.edit_calls.append((text, kwargs))
        return self


class FakeMessage:
    def __init__(self, text: str, sender_id: int = 10):
        self.id = 1
        self.chat_id = -1001
        self.raw_text = text
        self.sender_id = sender_id
        self.reply_to_msg_id = None
        self.replies: list[FakeAnswer] = []

    async def get_reply_message(self):
        return None

    async def reply(self, text: str, **kwargs):
        answer = FakeAnswer(text)
        self.replies.append(answer)
        return answer


class FakeGateway:
    def __init__(self, chunks=(), error: Exception | None = None):
        self.chunks = chunks
        self.error = error
        self.requests: list[AgentRunRequest] = []

    async def run(self, request: AgentRunRequest) -> AsyncIterator[AgentEvent]:
        self.requests.append(request)
        if self.error:
            raise self.error
        yield AgentEvent(
            type="run_started",
            run_id=request.run_id,
            session_id="session-1",
        )
        text = ""
        for index, chunk in enumerate(self.chunks):
            text += chunk
            yield AgentEvent(type="text_delta", delta=chunk, reset=index == 0)
        yield AgentEvent(
            type="run_completed",
            session_id="session-1",
            entry_id="entry-1",
            answer=text,
        )

    async def cancel(self, run_id: str) -> bool:
        return True


class FakeStore:
    def __init__(self):
        self.saved = []

    async def get_answer(self, chat_id, answer_message_id):
        return None

    async def save_answer(self, marker):
        self.saved.append(marker)

    async def is_allowed(self, user_id):
        return False

    async def get_last_request_at(self, user_id):
        return None

    async def set_last_request_at(self, user_id, timestamp):
        return None

    async def allow_user(self, user_id):
        return None

    async def deny_user(self, user_id):
        return None


def make_handler(owner_id, responder):
    return AIConversationHandler(
        owner_id=owner_id,
        responder=responder,
        store=FakeStore(),
        prompt_builder=PromptBuilder(),
    )


def make_request(prompt: str) -> AgentRunRequest:
    return AgentRunRequest(
        run_id="11111111-1111-4111-8111-111111111111",
        session_id=None,
        parent_entry_id=None,
        prompt=prompt,
        context=(),
        system_prompt=PromptBuilder().system_prompt,
        tool_policy="owner",
    )


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("/ai hello", "hello"),
        ("/ai\nhello", "hello"),
        ("/ai", ""),
        (" /ai hello", None),
        ("/air hello", None),
        ("hello /ai", None),
    ],
)
def test_parse_ai_trigger_has_an_exact_command_boundary(text, expected):
    assert parse_ai_trigger(text) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("/ai_memory remember this", "remember this"),
        ("/ai_memory\nforget that", "forget that"),
        ("/ai_memory", ""),
        ("/ai_memoryx no", None),
    ],
)
def test_parse_memory_revision_has_an_exact_command_boundary(text, expected):
    assert parse_memory_revision(text) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (
            "/ai_memory_backfill days 7",
            MemoryBackfillRequest(mode="days", value=7),
        ),
        (
            "/ai_memory_backfill\nmessages\t500",
            MemoryBackfillRequest(mode="messages", value=500),
        ),
        ("/ai_memory_backfill days 0", None),
        ("/ai_memory_backfill days 31", None),
        ("/ai_memory_backfill messages 5001", None),
        ("/ai_memory_backfill weeks 2", None),
        ("/ai_memory_backfill days 7 extra", None),
        ("/ai_memory_backfillx days 7", None),
    ],
)
def test_parse_memory_backfill_has_bounded_exact_syntax(text, expected):
    assert parse_memory_backfill(text) == expected


def test_ai_settings_are_loaded_without_provider_specific_assumptions(monkeypatch):
    values = {
        "TELEFIRE_PI_URL": "http://agent.test:8790/",
        "TELEFIRE_PI_TOKEN": "test-agent-token",
        "TELEFIRE_AI_MAX_OUTPUT_CHARS": "1234",
        "TELEFIRE_AI_EDIT_CADENCE": "0.25",
        "TELEFIRE_PI_RUN_TIMEOUT": "12",
    }
    for name, value in values.items():
        monkeypatch.setenv(name, value)

    settings = AISettings.from_env()

    assert settings.agent_url == "http://agent.test:8790"
    assert settings.agent_token == "test-agent-token"
    assert settings.max_output_chars == 1234
    assert settings.edit_cadence == 0.25
    assert settings.request_timeout == 12
    assert settings.hindsight_timeout == 90


def test_ai_command_is_registered_under_telegram():
    assert command_registry.as_fire_commands()["telegram"]["ai"]


@pytest.mark.asyncio
async def test_owner_gets_one_progressively_edited_answer():
    gateway = FakeGateway(["Hello", " ", "world"])
    times = iter([0.0, 1.0, 2.0, 3.0])
    responder = AIResponder(gateway, edit_cadence=0.5, clock=lambda: next(times))
    handler = make_handler(owner_id=10, responder=responder)
    trigger = FakeMessage("/ai greet me")

    handled = await handler.handle(trigger)

    assert handled is True
    assert len(trigger.replies) == 1
    assert trigger.replies[0].text == "Hello world"
    assert trigger.replies[0].edits[-1] == "Hello world"
    assert any(edit == "Hello" for edit in trigger.replies[0].edits)
    assert len(gateway.requests) == 1
    assert gateway.requests[0].prompt == "greet me"
    assert gateway.requests[0].system_prompt == PromptBuilder().system_prompt
    assert gateway.requests[0].tool_policy == "owner"


@pytest.mark.asyncio
async def test_edit_cadence_is_shared_across_answers(monkeypatch):
    sleeps = []

    async def fake_sleep(seconds):
        sleeps.append(seconds)

    monkeypatch.setattr("telefire.ai.asyncio.sleep", fake_sleep)
    gateway = FakeGateway(["answer"])
    responder = AIResponder(gateway, edit_cadence=4, clock=lambda: 0.0)
    first = FakeMessage("/ai first")
    second = FakeMessage("/ai second")

    await responder.answer(first, make_request("first"))
    await responder.answer(second, make_request("second"))

    assert first.replies[0].text == "answer"
    assert second.replies[0].text == "answer"
    assert sleeps == [4]


@pytest.mark.asyncio
async def test_flood_wait_delays_final_edit_without_replacing_the_answer(monkeypatch):
    sleeps = []

    async def fake_sleep(seconds):
        sleeps.append(seconds)

    class FloodOnceAnswer(FakeAnswer):
        def __init__(self, text):
            super().__init__(text)
            self.attempts = 0

        async def edit(self, text: str, **kwargs):
            self.attempts += 1
            if self.attempts == 1:
                raise FloodWaitError(request=None, capture=7)
            return await super().edit(text, **kwargs)

    class FloodOnceMessage(FakeMessage):
        async def reply(self, text: str, **kwargs):
            answer = FloodOnceAnswer(text)
            self.replies.append(answer)
            return answer

    monkeypatch.setattr("telefire.ai.asyncio.sleep", fake_sleep)
    responder = AIResponder(
        FakeGateway(["final answer"]),
        edit_cadence=0,
        clock=lambda: 0.0,
    )
    trigger = FloodOnceMessage("/ai answer")

    result = await responder.answer(trigger, make_request("answer"))

    assert result.succeeded is True
    assert result.text == "final answer"
    assert trigger.replies[0].text == "final answer"
    assert sleeps == [7]


def test_prompt_builder_appends_the_regular_telegram_format_guard():
    builder = PromptBuilder(system_prompt="Keep answers factual.")

    assert builder.system_prompt.startswith("Keep answers factual.")
    assert "Telegram regular-message HTML" in builder.system_prompt
    assert "<b>bold</b>" in builder.system_prompt
    assert "<i>italic</i>" in builder.system_prompt
    assert "<blockquote>quoted text</blockquote>" in builder.system_prompt
    assert "<pre>" in builder.system_prompt
    assert "Do not emit Markdown markers" in builder.system_prompt


def test_response_format_switches_only_for_a_bot_rich_transport():
    from telefire.ai import select_telegram_response_format

    assert (
        select_telegram_response_format(
            is_bot_account=False,
            rich_messages_available=True,
        )
        == "regular_html"
    )
    assert (
        select_telegram_response_format(
            is_bot_account=True,
            rich_messages_available=False,
        )
        == "regular_html"
    )
    assert (
        select_telegram_response_format(
            is_bot_account=True,
            rich_messages_available=True,
        )
        == "rich_markdown"
    )

    rich_builder = PromptBuilder(
        system_prompt="Keep answers factual.",
        response_format="rich_markdown",
    )
    assert "Telegram Bot API rich-message Markdown" in rich_builder.system_prompt
    assert "| Header 1 | Header 2 |" in rich_builder.system_prompt


@pytest.mark.asyncio
async def test_bot_response_uses_telegram_rich_markdown_edit():
    class FakeTelegramClient:
        def __init__(self):
            self.requests = []

        async def __call__(self, request):
            self.requests.append(request)

    class FakeRichAnswer(FakeAnswer):
        def __init__(self, text):
            super().__init__(text)
            self.client = FakeTelegramClient()

        async def get_input_chat(self):
            return telegram_types.InputPeerSelf()

    class FakeRichMessage(FakeMessage):
        async def reply(self, text: str, **kwargs):
            answer = FakeRichAnswer(text)
            self.replies.append(answer)
            return answer

    formatted = "**Result**\n\n| Key | Value |\n|:----|:------|\n| Mode | Rich |"
    gateway = FakeGateway([formatted])
    responder = AIResponder(
        gateway,
        edit_cadence=0,
        response_format="rich_markdown",
    )
    trigger = FakeRichMessage("/ai format this")

    result = await responder.answer(trigger, make_request("format this"))

    answer = trigger.replies[0]
    assert result.text == formatted
    assert answer.edit_calls == []
    request = answer.client.requests[-1]
    assert isinstance(request, telegram_functions.messages.EditMessageRequest)
    assert isinstance(request.rich_message, telegram_types.InputRichMessageMarkdown)
    assert request.rich_message.markdown == formatted


@pytest.mark.asyncio
async def test_streamed_html_is_sent_as_native_telegram_entities():
    formatted = (
        "<b>Result</b>\n"
        "<i>Estimate</i>\n"
        "<blockquote>Supporting context</blockquote>\n"
        "<pre>Team     Score\nNorway   1\nEngland  2</pre>"
    )
    gateway = FakeGateway([formatted])
    responder = AIResponder(gateway, edit_cadence=0)
    trigger = FakeMessage("/ai format this")

    result = await responder.answer(trigger, make_request("format this"))

    answer = trigger.replies[0]
    assert result.text == formatted
    assert answer.text == (
        "Result\nEstimate\nSupporting context\nTeam     Score\nNorway   1\nEngland  2"
    )
    _, kwargs = answer.edit_calls[-1]
    assert kwargs["parse_mode"] is None
    assert {type(entity).__name__ for entity in kwargs["formatting_entities"]} == {
        "MessageEntityBold",
        "MessageEntityItalic",
        "MessageEntityBlockquote",
        "MessageEntityPre",
    }


@pytest.mark.asyncio
async def test_streaming_waits_for_visible_text_when_an_html_tag_is_split():
    gateway = FakeGateway(["<b>", "Result", "</b>"])
    responder = AIResponder(gateway, edit_cadence=0)
    trigger = FakeMessage("/ai format this")

    result = await responder.answer(trigger, make_request("format this"))

    answer = trigger.replies[0]
    assert result.succeeded is True
    assert all(text for text, _ in answer.edit_calls)
    assert answer.text == "Result"
    assert {
        type(entity).__name__
        for entity in answer.edit_calls[-1][1]["formatting_entities"]
    } == {"MessageEntityBold"}


@pytest.mark.asyncio
async def test_unauthorized_trigger_is_silent_and_does_not_call_provider():
    gateway = FakeGateway(["must not be used"])
    handler = make_handler(
        owner_id=10,
        responder=AIResponder(gateway, edit_cadence=0),
    )
    trigger = FakeMessage("/ai secret", sender_id=11)

    assert await handler.handle(trigger) is False
    assert trigger.replies == []
    assert gateway.requests == []


@pytest.mark.asyncio
async def test_owner_ai_requests_are_not_blocked_by_chat_scope():
    gateway = FakeGateway(["answer"])
    handler = AIConversationHandler(
        owner_id=10,
        responder=AIResponder(gateway, edit_cadence=0),
        store=FakeStore(),
        prompt_builder=PromptBuilder(),
    )
    trigger = FakeMessage("/ai secret")
    trigger.chat_id = -1002

    assert await handler.handle(trigger) is True
    assert trigger.replies[0].text == "answer"
    assert len(gateway.requests) == 1


@pytest.mark.asyncio
async def test_empty_prompt_finishes_with_usage_without_calling_provider():
    gateway = FakeGateway(["must not be used"])
    handler = make_handler(
        owner_id=10,
        responder=AIResponder(gateway, edit_cadence=0),
    )
    trigger = FakeMessage("/ai")

    assert await handler.handle(trigger) is True
    assert len(trigger.replies) == 1
    assert trigger.replies[0].text == "Usage: /ai <question>"
    assert gateway.requests == []


@pytest.mark.asyncio
async def test_provider_failure_replaces_loading_message():
    gateway = FakeGateway(error=RuntimeError("provider secret detail"))
    handler = make_handler(
        owner_id=10,
        responder=AIResponder(gateway, edit_cadence=0),
    )
    trigger = FakeMessage("/ai hello")

    assert await handler.handle(trigger) is True
    assert len(trigger.replies) == 1
    assert trigger.replies[0].text == "AI request failed. Try again later."
    assert gateway.requests


@pytest.mark.asyncio
async def test_provider_failure_uses_standard_logging_format(caplog):
    import logging

    gateway = FakeGateway(error=RuntimeError("provider detail"))
    responder = AIResponder(
        gateway,
        edit_cadence=0,
        logger=logging.getLogger("telefire-ai-test"),
    )
    trigger = FakeMessage("/ai hello")

    await responder.answer(trigger, make_request("hello"))

    assert "AI agent request failed (RuntimeError)" in caplog.text


@pytest.mark.asyncio
async def test_output_is_bounded_and_finalized():
    gateway = FakeGateway(["abcdefghijk"])
    responder = AIResponder(gateway, edit_cadence=0, max_output_chars=10)
    trigger = FakeMessage("/ai long")

    await responder.answer(trigger, make_request("long"))

    assert trigger.replies[0].text == "abcdefg..."
