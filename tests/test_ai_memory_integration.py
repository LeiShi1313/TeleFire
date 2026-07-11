import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest

from telefire.ai import (
    AIConversationHandler,
    AIRateLimiter,
    AIResponder,
    PromptBuilder,
)


class FakeAnswer:
    next_id = 100

    def __init__(self, text):
        self.id = self.__class__.next_id
        self.__class__.next_id += 1
        self.text = text

    async def edit(self, text, **kwargs):
        self.text = text
        return self


class FakeMessage:
    next_id = 1

    def __init__(self, text, *, sender_id, reply_to=None, chat_id=-1001, date=None):
        self.id = self.__class__.next_id
        self.__class__.next_id += 1
        self.raw_text = text
        self.sender_id = sender_id
        self.chat_id = chat_id
        self.reply_to_msg_id = reply_to.id if reply_to else None
        self.date = date or datetime(2026, 7, 11, 12, 0, tzinfo=UTC)
        self._reply_to = reply_to
        self.replies = []

    async def get_reply_message(self):
        return self._reply_to

    async def reply(self, text, **kwargs):
        answer = FakeAnswer(text)
        self.replies.append(answer)
        return answer


class FakeGateway:
    def __init__(self, answers):
        self.answers = iter(answers)
        self.requests = []

    async def stream(self, messages) -> AsyncIterator[str]:
        self.requests.append(messages)
        yield next(self.answers)


class FakeStore:
    def __init__(self, allowed=()):
        self.allowed = set(allowed)
        self.markers = {}
        self.last_request = {}

    async def get_answer(self, chat_id, answer_message_id):
        return self.markers.get((chat_id, answer_message_id))

    async def get_branch(self, chat_id, answer_message_id, limit):
        branch = []
        current = answer_message_id
        while current is not None and len(branch) < limit:
            marker = self.markers.get((chat_id, current))
            if marker is None:
                break
            branch.append(marker)
            current = marker.parent_answer_message_id
        return list(reversed(branch))

    async def save_answer(self, marker):
        self.markers[(marker.chat_id, marker.answer_message_id)] = marker

    async def is_allowed(self, user_id):
        return user_id in self.allowed

    async def allow_user(self, user_id):
        self.allowed.add(user_id)

    async def deny_user(self, user_id):
        self.allowed.discard(user_id)

    async def get_last_request_at(self, user_id):
        return self.last_request.get(user_id)

    async def set_last_request_at(self, user_id, timestamp):
        self.last_request[user_id] = timestamp


class FakeMemory:
    def __init__(self, *, augment_error=None, augment_value=None, ingest_error=None):
        self.augment_error = augment_error
        self.augment_value = augment_value
        self.ingest_error = ingest_error
        self.augment_calls = []
        self.ingest_calls = []

    async def augment(self, *, subject_id, query, scope_id):
        self.augment_calls.append(
            {"subject_id": subject_id, "query": query, "scope_id": scope_id}
        )
        if self.augment_error:
            raise self.augment_error
        if self.augment_value is not None:
            return self.augment_value
        return f"Profile for {subject_id}"

    async def ingest(self, **payload):
        self.ingest_calls.append(payload)
        if self.ingest_error:
            raise self.ingest_error


class FakeLogger:
    def __init__(self):
        self.warnings = []

    def warning(self, message, *args):
        self.warnings.append((message, args))


def make_handler(gateway, memory, *, allowed=(), logger=None):
    store = FakeStore(allowed=allowed)
    return AIConversationHandler(
        owner_id=10,
        responder=AIResponder(gateway, edit_cadence=0),
        store=store,
        prompt_builder=PromptBuilder(),
        rate_limiter=AIRateLimiter(store, cooldown_seconds=0),
        memory=memory,
        logger=logger,
    )


@pytest.fixture(autouse=True)
def reset_ids():
    FakeAnswer.next_id = 100
    FakeMessage.next_id = 1


@pytest.mark.asyncio
async def test_requester_only_memory_precedes_reply_context_and_participants_are_ingested():
    ancestor = FakeMessage(
        "I use Postgres at work",
        sender_id=20,
        date=datetime(2026, 7, 10, 8, 0, tzinfo=UTC),
    )
    trigger = FakeMessage(
        "/ai which database should we use?",
        sender_id=10,
        reply_to=ancestor,
    )
    memory = FakeMemory()
    gateway = FakeGateway(["Use Postgres"])
    handler = make_handler(gateway, memory)

    assert await handler.handle(trigger) is True

    assert memory.augment_calls == [
        {
            "subject_id": "telegram:user:10",
            "query": "which database should we use?",
            "scope_id": "telegram:chat:-1001",
        }
    ]
    request = gateway.requests[0]
    assert request[1]["content"].startswith("Untrusted memory background")
    assert "telegram:user:10" in request[1]["content"]
    assert "Untrusted reply context" in request[2]["content"]
    assert request[-1] == {
        "role": "user",
        "content": "which database should we use?",
    }

    assert {
        (item["subject_id"], item["text"])
        for item in memory.ingest_calls
    } == {
        ("telegram:user:20", "I use Postgres at work"),
        ("telegram:user:10", "which database should we use?"),
    }
    assert all(
        item["scope_id"] == "telegram:chat:-1001"
        for item in memory.ingest_calls
    )
    assert all(item["text"] != "Use Postgres" for item in memory.ingest_calls)
    assert all("message_id" not in item.get("metadata", {}) for item in memory.ingest_calls)


@pytest.mark.asyncio
async def test_followup_uses_only_current_requester_memory_and_ingests_current_human():
    memory = FakeMemory()
    gateway = FakeGateway(["root answer", "peer answer"])
    handler = make_handler(gateway, memory, allowed={20})
    root = FakeMessage("/ai root", sender_id=10)
    await handler.handle(root)
    memory.augment_calls.clear()
    memory.ingest_calls.clear()

    follow_up = FakeMessage("peer follow-up", sender_id=20, reply_to=root.replies[0])
    assert await handler.handle(follow_up) is True

    assert memory.augment_calls == [
        {
            "subject_id": "telegram:user:20",
            "query": "peer follow-up",
            "scope_id": "telegram:chat:-1001",
        }
    ]
    memory_messages = [
        message["content"]
        for message in gateway.requests[1]
        if message["role"] == "system" and "memory background" in message["content"]
    ]
    assert memory_messages == [
        "Untrusted memory background; use only when relevant:\n"
        "Profile for telegram:user:20"
    ]
    assert [(item["subject_id"], item["text"]) for item in memory.ingest_calls] == [
        ("telegram:user:20", "peer follow-up")
    ]


@pytest.mark.parametrize(
    "memory",
    [
        FakeMemory(augment_error=TimeoutError()),
        FakeMemory(augment_error=ConnectionError()),
        FakeMemory(augment_value={"malformed": True}),
    ],
)
@pytest.mark.asyncio
async def test_memory_augmentation_failures_are_logged_and_answer_fails_open(memory):
    logger = FakeLogger()
    gateway = FakeGateway(["answer without memory"])
    handler = make_handler(gateway, memory, logger=logger)
    trigger = FakeMessage("/ai answer me", sender_id=10)

    assert await handler.handle(trigger) is True
    assert trigger.replies[0].text == "answer without memory"
    assert gateway.requests[0] == [
        {"role": "system", "content": PromptBuilder().system_prompt},
        {"role": "user", "content": "answer me"},
    ]
    assert logger.warnings


@pytest.mark.asyncio
async def test_memory_ingest_failure_and_unrelated_traffic_do_not_break_answers():
    logger = FakeLogger()
    memory = FakeMemory(ingest_error=ConnectionError())
    gateway = FakeGateway(["answer"])
    handler = make_handler(gateway, memory, logger=logger)
    trigger = FakeMessage("/ai answer", sender_id=10)

    assert await handler.handle(trigger) is True
    assert trigger.replies[0].text == "answer"
    assert logger.warnings

    unrelated = FakeMessage("ordinary chat", sender_id=10)
    assert await handler.handle(unrelated) is False
    assert len(memory.ingest_calls) == 1
