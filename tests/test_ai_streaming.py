from collections.abc import AsyncIterator

import pytest

from telefire.ai import (
    AIConversationHandler,
    AIResponder,
    AISettings,
    PromptBuilder,
    parse_ai_trigger,
    parse_memory_revision,
)
from telefire.plugins.base import command_registry
import telefire.plugins.ai  # noqa: F401


class FakeAnswer:
    def __init__(self, text: str):
        self.id = 100
        self.text = text
        self.edits: list[str] = []

    async def edit(self, text: str, **kwargs):
        self.text = text
        self.edits.append(text)
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
        self.requests: list[list[dict[str, str]]] = []

    async def stream(self, messages: list[dict[str, str]]) -> AsyncIterator[str]:
        self.requests.append(messages)
        if self.error:
            raise self.error
        for chunk in self.chunks:
            yield chunk


class FakeStore:
    def __init__(self):
        self.saved = []

    async def get_answer(self, chat_id, answer_message_id):
        return None

    async def get_branch(self, chat_id, answer_message_id, limit):
        return []

    async def save_answer(self, marker):
        self.saved.append(marker)

    async def is_allowed(self, user_id):
        return False

    async def get_last_request_at(self, user_id):
        return None

    async def set_last_request_at(self, user_id, timestamp):
        return None


def make_handler(owner_id, responder):
    return AIConversationHandler(
        owner_id=owner_id,
        responder=responder,
        store=FakeStore(),
        prompt_builder=PromptBuilder(),
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


def test_ai_settings_are_loaded_without_provider_specific_assumptions(monkeypatch):
    values = {
        "TELEFIRE_AI_BASE_URL": "http://provider.test/v1",
        "TELEFIRE_AI_API_KEY": "test-key",
        "TELEFIRE_AI_CHAT_MODEL": "test-model",
        "TELEFIRE_AI_MAX_OUTPUT_TOKENS": "321",
        "TELEFIRE_AI_MAX_OUTPUT_CHARS": "1234",
        "TELEFIRE_AI_EDIT_CADENCE": "0.25",
        "TELEFIRE_AI_REQUEST_TIMEOUT": "12",
    }
    for name, value in values.items():
        monkeypatch.setenv(name, value)

    settings = AISettings.from_env()

    assert settings.base_url == "http://provider.test/v1"
    assert settings.api_key == "test-key"
    assert settings.chat_model == "test-model"
    assert settings.max_output_tokens == 321
    assert settings.max_output_chars == 1234
    assert settings.edit_cadence == 0.25
    assert settings.request_timeout == 12


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
    assert gateway.requests == [[
        {"role": "system", "content": AISettings.DEFAULT_SYSTEM_PROMPT},
        {"role": "user", "content": "greet me"},
    ]]


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
async def test_output_is_bounded_and_finalized():
    gateway = FakeGateway(["abcdefghijk"])
    responder = AIResponder(gateway, edit_cadence=0, max_output_chars=10)
    trigger = FakeMessage("/ai long")

    await responder.answer(trigger, "long")

    assert trigger.replies[0].text == "abcdefg..."
