import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest
from telethon.tl import types as telegram_types

from telefire.ai import (
    AIAnswerMarker,
    AIConversationHandler,
    AIRateLimiter,
    AIResponder,
    AgentEvent,
    AgentRunRequest,
    MessageIdentity,
    PromptBuilder,
    TelegramMessageIdentityResolver,
)
from telefire.ai_attachments import AttachmentDescription
from telefire.ai_memory import MemoryIngestResult


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

    def __init__(
        self,
        text,
        *,
        sender_id,
        reply_to=None,
        chat_id=-1001,
        date=None,
        file=None,
    ):
        self.id = self.__class__.next_id
        self.__class__.next_id += 1
        self.raw_text = text
        self.sender_id = sender_id
        self.chat_id = chat_id
        self.reply_to_msg_id = reply_to.id if reply_to else None
        self.date = date or datetime(2026, 7, 11, 12, 0, tzinfo=UTC)
        self.file = file
        self._reply_to = reply_to
        self.replies = []
        self.deleted = False

    async def get_reply_message(self):
        return self._reply_to

    async def reply(self, text, **kwargs):
        answer = FakeAnswer(text)
        self.replies.append(answer)
        return answer

    async def delete(self):
        self.deleted = True


class FakeGateway:
    def __init__(self, answers):
        self.answers = iter(answers)
        self.requests = []

    async def run(self, request: AgentRunRequest) -> AsyncIterator[AgentEvent]:
        self.requests.append(request)
        answer = next(self.answers)
        session_id = request.session_id or f"session-{len(self.requests)}"
        yield AgentEvent(type="run_started", session_id=session_id)
        yield AgentEvent(type="text_delta", delta=answer, reset=True)
        yield AgentEvent(
            type="run_completed",
            session_id=session_id,
            entry_id=f"entry-{len(self.requests)}",
            answer=answer,
        )

    async def cancel(self, run_id: str) -> bool:
        return True


class FakeStore:
    def __init__(self, allowed=()):
        self.allowed = set(allowed)
        self.markers = {}
        self.last_request = {}

    async def get_answer(self, chat_id, answer_message_id):
        return self.markers.get((chat_id, answer_message_id))

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
    def __init__(
        self,
        *,
        augment_error=None,
        augment_value=None,
        ingest_error=None,
        ingest_result=None,
        revise_error=None,
    ):
        self.augment_error = augment_error
        self.augment_value = augment_value
        self.ingest_error = ingest_error
        self.ingest_result = ingest_result or MemoryIngestResult(
            created=True,
            facts_added=1,
            episodes_added=1,
        )
        self.revise_error = revise_error
        self.augment_calls = []
        self.ingest_calls = []
        self.revise_calls = []
        self.identity_calls = []

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
        return self.ingest_result

    async def upsert_identities(self, identities):
        self.identity_calls.append(identities)
        return len(identities)

    async def revise(self, **payload):
        self.revise_calls.append(payload)
        if self.revise_error:
            raise self.revise_error
        return {"profile_updated": True, "suppressed_count": 0}


class PerSubjectMemory(FakeMemory):
    def __init__(self, contexts):
        super().__init__()
        self.contexts = contexts

    async def augment(self, *, subject_id, query, scope_id):
        self.augment_calls.append(
            {"subject_id": subject_id, "query": query, "scope_id": scope_id}
        )
        return self.contexts.get(subject_id, "")


class FakeLogger:
    def __init__(self):
        self.warnings = []

    def warning(self, message, *args):
        self.warnings.append((message, args))


class FakeAttachmentDescriber:
    def __init__(self, descriptions=None):
        self.descriptions = descriptions or {}
        self.calls = []

    async def describe(self, message):
        self.calls.append(message.id)
        return self.descriptions.get(message.id)


class FakeIdentityResolver:
    async def resolve(self, message):
        return MessageIdentity(
            subject_display_name=f"User {message.sender_id}",
            scope_display_name="Engineering Group",
        )


class FakeTelegramIdentityMessage:
    async def get_sender(self):
        return telegram_types.User(
            id=20,
            first_name="Alice",
            last_name="Example",
        )

    async def get_chat(self):
        return telegram_types.Channel(
            id=1001,
            title="Engineering Group",
            photo=telegram_types.ChatPhotoEmpty(),
            date=None,
        )


def make_handler(
    gateway,
    memory,
    *,
    allowed=(),
    logger=None,
    attachment_describer=None,
    identity_resolver=None,
    store=None,
):
    store = store or FakeStore(allowed=allowed)
    return AIConversationHandler(
        owner_id=10,
        responder=AIResponder(gateway, edit_cadence=0),
        store=store,
        prompt_builder=PromptBuilder(
            attachment_describer=attachment_describer,
            identity_resolver=identity_resolver,
        ),
        rate_limiter=AIRateLimiter(store, cooldown_seconds=0),
        memory=memory,
        logger=logger,
    )


@pytest.fixture(autouse=True)
def reset_ids():
    FakeAnswer.next_id = 100
    FakeMessage.next_id = 1


@pytest.mark.asyncio
async def test_telegram_identity_resolver_uses_entity_display_names():
    identity = await TelegramMessageIdentityResolver().resolve(
        FakeTelegramIdentityMessage()
    )

    assert identity == MessageIdentity(
        subject_display_name="Alice Example",
        scope_display_name="Engineering Group",
    )


@pytest.mark.asyncio
async def test_requester_and_reply_participant_memory_precede_context_and_are_ingested():
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
    handler = make_handler(
        gateway,
        memory,
        identity_resolver=FakeIdentityResolver(),
    )

    assert await handler.handle(trigger) is True

    assert memory.augment_calls == [
        {
            "subject_id": "telegram:user:10",
            "query": "which database should we use?",
            "scope_id": "telegram:chat:-1001",
        },
        {
            "subject_id": "telegram:user:20",
            "query": "which database should we use?",
            "scope_id": "telegram:chat:-1001",
        },
    ]
    request = gateway.requests[0]
    assert [item.kind for item in request.context] == ["memory", "reply"]
    assert "telegram:user:10" in request.context[0].text
    assert "telegram:user:20" in request.context[0].text
    assert "Untrusted reply context" in request.context[1].text
    assert request.prompt == "which database should we use?"

    assert {(item["subject_id"], item["text"]) for item in memory.ingest_calls} == {
        ("telegram:user:20", "I use Postgres at work"),
        ("telegram:user:10", "which database should we use?"),
    }
    assert all(
        item["scope_id"] == "telegram:chat:-1001" for item in memory.ingest_calls
    )
    assert all(item["text"] != "Use Postgres" for item in memory.ingest_calls)
    assert all(
        "message_id" not in item.get("metadata", {}) for item in memory.ingest_calls
    )
    assert memory.identity_calls == [
        {
            "telegram:user:10": "User 10",
            "telegram:user:20": "User 20",
            "telegram:chat:-1001": "Engineering Group",
        }
    ]


@pytest.mark.asyncio
async def test_ai_generated_chain_messages_are_context_but_not_memory_subjects():
    human = FakeMessage("I prefer Rust", sender_id=20)
    ai_output = FakeMessage(
        "Generated claim that must not become owner memory",
        sender_id=10,
        reply_to=human,
    )
    trigger = FakeMessage(
        "/ai what does the participant prefer?",
        sender_id=10,
        reply_to=ai_output,
    )
    store = FakeStore()
    store.markers[(ai_output.chat_id, ai_output.id)] = AIAnswerMarker(
        chat_id=ai_output.chat_id,
        answer_message_id=ai_output.id,
        trigger_message_id=999,
        requester_id=20,
        prompt="old prompt",
        answer_text=ai_output.raw_text,
        parent_answer_message_id=None,
        reference_context="",
        agent_session_id="session-old",
        agent_entry_id="entry-old",
    )
    memory = FakeMemory()
    gateway = FakeGateway(["Rust"])
    handler = make_handler(gateway, memory, store=store)

    assert await handler.handle(trigger) is True

    reply_context = next(
        item.text for item in gateway.requests[0].context if item.kind == "reply"
    )
    assert "Generated claim" in reply_context
    ingested = {(item["subject_id"], item["text"]) for item in memory.ingest_calls}
    assert ("telegram:user:20", "I prefer Rust") in ingested
    assert all("Generated claim" not in text for _, text in ingested)


@pytest.mark.asyncio
async def test_participant_memory_cannot_be_crowded_out_by_requester_memory():
    ancestor = FakeMessage("I use Postgres", sender_id=20)
    trigger = FakeMessage("/ai choose a database", sender_id=10, reply_to=ancestor)
    memory = PerSubjectMemory(
        {
            "telegram:user:10": "R" * 4_000,
            "telegram:user:20": "Participant prefers Postgres",
        }
    )
    gateway = FakeGateway(["Postgres"])
    handler = make_handler(gateway, memory)

    assert await handler.handle(trigger) is True

    rendered = next(
        item.text for item in gateway.requests[0].context if item.kind == "memory"
    )
    assert "Participant prefers Postgres" in rendered
    assert len(rendered) <= 4_100


@pytest.mark.asyncio
async def test_attachment_descriptions_augment_context_and_memory_by_author():
    ancestor = FakeMessage("", sender_id=20, file=object())
    trigger = FakeMessage(
        "/ai compare the attachments",
        sender_id=10,
        reply_to=ancestor,
        file=object(),
    )
    describer = FakeAttachmentDescriber(
        {
            ancestor.id: AttachmentDescription(
                context_text="Generated attachment context: architecture diagram",
                memory_text=(
                    "The subject shared an attachment. Generated content description: "
                    "architecture diagram"
                ),
            ),
            trigger.id: AttachmentDescription(
                context_text="Generated attachment context: deployment checklist",
                memory_text=(
                    "The subject shared an attachment. Generated content description: "
                    "deployment checklist"
                ),
            ),
        }
    )
    memory = FakeMemory(augment_value="")
    gateway = FakeGateway(["comparison"])
    handler = make_handler(
        gateway,
        memory,
        attachment_describer=describer,
    )

    assert await handler.handle(trigger) is True

    rendered_context = "\n".join(item.text for item in gateway.requests[0].context)
    assert "architecture diagram" in rendered_context
    assert "deployment checklist" in rendered_context
    by_subject = {item["subject_id"]: item["text"] for item in memory.ingest_calls}
    assert "architecture diagram" in by_subject["telegram:user:20"]
    assert "deployment checklist" in by_subject["telegram:user:10"]
    assert "compare the attachments" in by_subject["telegram:user:10"]


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
        item.text for item in gateway.requests[1].context if item.kind == "memory"
    ]
    assert len(memory_messages) == 1
    assert "Profile for telegram:user:20" in memory_messages[0]
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
    assert gateway.requests[0].system_prompt == PromptBuilder().system_prompt
    assert gateway.requests[0].prompt == "answer me"
    assert gateway.requests[0].context == ()
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


@pytest.mark.parametrize(
    "instruction",
    [
        "Remember that this user likes tea",
        "Correct the preference to coffee",
        "Forget tea",
    ],
)
@pytest.mark.asyncio
async def test_owner_revises_replied_user_and_ingests_human_reply_chain(
    instruction,
):
    memory = FakeMemory()
    gateway = FakeGateway(["unused"])
    handler = make_handler(gateway, memory)
    ancestor = FakeMessage("I use Python at work", sender_id=30)
    target = FakeMessage("I prefer tea", sender_id=20, reply_to=ancestor)
    command = FakeMessage(
        f"/ai_memory {instruction}",
        sender_id=10,
        reply_to=target,
    )

    assert await handler.handle(command) is True
    assert command.replies[0].text == "Memory updated."
    assert memory.revise_calls == [
        {
            "subject_id": "telegram:user:20",
            "instruction": instruction,
            "evidence": ("user:30: I use Python at work\n\nuser:20: I prefer tea"),
            "scope_id": "telegram:chat:-1001",
        }
    ]
    assert [(item["subject_id"], item["text"]) for item in memory.ingest_calls] == [
        ("telegram:user:30", "I use Python at work"),
        ("telegram:user:20", "I prefer tea"),
    ]
    assert memory.augment_calls == []
    assert gateway.requests == []
    assert command.deleted is False


@pytest.mark.asyncio
async def test_bare_memory_command_ingests_human_reply_chain_with_identities():
    memory = FakeMemory(
        ingest_result=MemoryIngestResult(
            created=True,
            facts_added=1,
            episodes_added=2,
        )
    )
    gateway = FakeGateway(["unused"])
    handler = make_handler(
        gateway,
        memory,
        identity_resolver=FakeIdentityResolver(),
    )
    ancestor = FakeMessage(
        "I use Python at work",
        sender_id=30,
        date=datetime(2026, 7, 12, 8, 0, tzinfo=UTC),
    )
    target = FakeMessage(
        "I started using Rust today",
        sender_id=20,
        reply_to=ancestor,
        date=datetime(2026, 7, 12, 8, 30, tzinfo=UTC),
    )
    command = FakeMessage("/ai_memory", sender_id=10, reply_to=target)

    assert await handler.handle(command) is True

    assert command.replies[0].text == (
        "Memory stored from reply chain: 2 messages, 2 facts, 4 episodes."
    )
    assert memory.ingest_calls == [
        {
            "subject_id": "telegram:user:30",
            "scope_id": "telegram:chat:-1001",
            "text": "I use Python at work",
            "occurred_at": datetime(2026, 7, 12, 8, 0, tzinfo=UTC),
            "metadata": {"client": "telefire", "source": "chat_message"},
        },
        {
            "subject_id": "telegram:user:20",
            "scope_id": "telegram:chat:-1001",
            "text": "I started using Rust today",
            "occurred_at": datetime(2026, 7, 12, 8, 30, tzinfo=UTC),
            "metadata": {"client": "telefire", "source": "chat_message"},
        },
    ]
    assert memory.identity_calls == [
        {
            "telegram:user:30": "User 30",
            "telegram:user:20": "User 20",
            "telegram:chat:-1001": "Engineering Group",
        }
    ]
    assert memory.revise_calls == []
    assert memory.augment_calls == []
    assert gateway.requests == []
    assert command.deleted is True


@pytest.mark.asyncio
async def test_saved_forward_memory_entrypoint_includes_source_and_ancestors():
    memory = FakeMemory()
    gateway = FakeGateway(["unused"])
    handler = make_handler(
        gateway,
        memory,
        identity_resolver=FakeIdentityResolver(),
    )
    ancestor = FakeMessage("I use Python at work", sender_id=30)
    source = FakeMessage(
        "I started using Rust today",
        sender_id=20,
        reply_to=ancestor,
    )

    assert await handler.remember_reply_chain(source) is True

    assert [(item["subject_id"], item["text"]) for item in memory.ingest_calls] == [
        ("telegram:user:30", "I use Python at work"),
        ("telegram:user:20", "I started using Rust today"),
    ]
    assert memory.identity_calls == [
        {
            "telegram:user:30": "User 30",
            "telegram:user:20": "User 20",
            "telegram:chat:-1001": "Engineering Group",
        }
    ]
    assert gateway.requests == []


@pytest.mark.asyncio
async def test_bare_memory_command_reports_an_existing_observation():
    memory = FakeMemory(
        ingest_result=MemoryIngestResult(
            created=False,
            facts_added=0,
            episodes_added=0,
        )
    )
    gateway = FakeGateway(["unused"])
    handler = make_handler(gateway, memory)
    target = FakeMessage("I prefer tea", sender_id=20)
    command = FakeMessage("/ai_memory", sender_id=10, reply_to=target)

    assert await handler.handle(command) is True
    assert command.replies[0].text == "Already remembered."
    assert len(memory.ingest_calls) == 1
    assert command.deleted is True


@pytest.mark.asyncio
async def test_bare_memory_command_skips_ai_answer_and_ingests_human_chain():
    memory = FakeMemory()
    gateway = FakeGateway(["unused"])
    store = FakeStore()
    human = FakeMessage("I prefer Rust", sender_id=20)
    target = FakeMessage("Generated answer", sender_id=10, reply_to=human)
    store.markers[(target.chat_id, target.id)] = AIAnswerMarker(
        chat_id=target.chat_id,
        answer_message_id=target.id,
        trigger_message_id=999,
        requester_id=20,
        prompt="question",
        answer_text="Generated answer",
        parent_answer_message_id=None,
        reference_context="",
        agent_session_id="session-old",
        agent_entry_id="entry-old",
    )
    handler = make_handler(gateway, memory, store=store)
    command = FakeMessage("/ai_memory", sender_id=10, reply_to=target)

    assert await handler.handle(command) is True
    assert command.replies[0].text == (
        "Memory stored from reply chain: 1 message, 1 fact, 1 episode."
    )
    assert [(item["subject_id"], item["text"]) for item in memory.ingest_calls] == [
        ("telegram:user:20", "I prefer Rust")
    ]
    assert memory.revise_calls == []
    assert command.deleted is True


@pytest.mark.asyncio
async def test_memory_revision_requires_a_direct_human_target():
    memory = FakeMemory()
    gateway = FakeGateway(["unused"])
    store = FakeStore()
    human = FakeMessage("I prefer Rust", sender_id=20)
    target = FakeMessage("Generated answer", sender_id=10, reply_to=human)
    store.markers[(target.chat_id, target.id)] = AIAnswerMarker(
        chat_id=target.chat_id,
        answer_message_id=target.id,
        trigger_message_id=999,
        requester_id=20,
        prompt="question",
        answer_text="Generated answer",
        parent_answer_message_id=None,
        reference_context="",
        agent_session_id="session-old",
        agent_entry_id="entry-old",
    )
    handler = make_handler(gateway, memory, store=store)
    command = FakeMessage(
        "/ai_memory Correct the preference to Go",
        sender_id=10,
        reply_to=target,
    )

    assert await handler.handle(command) is True
    assert command.replies[0].text == (
        "Reply directly to a human message when revising memory."
    )
    assert memory.ingest_calls == []
    assert memory.revise_calls == []
    assert command.deleted is False


@pytest.mark.asyncio
async def test_memory_revision_is_owner_only_and_failure_is_bounded():
    logger = FakeLogger()
    memory = FakeMemory(revise_error=ValueError("internal model output"))
    gateway = FakeGateway(["unused"])
    handler = make_handler(gateway, memory, allowed={20}, logger=logger)
    target = FakeMessage("evidence", sender_id=30)

    unauthorized = FakeMessage(
        "/ai_memory change profile",
        sender_id=20,
        reply_to=target,
    )
    assert await handler.handle(unauthorized) is False
    assert unauthorized.replies == []
    assert memory.revise_calls == []
    assert unauthorized.deleted is False

    owner = FakeMessage(
        "/ai_memory change profile",
        sender_id=10,
        reply_to=target,
    )
    assert await handler.handle(owner) is True
    assert owner.replies[0].text == "Memory revision failed. Retry the command."
    assert [(item["subject_id"], item["text"]) for item in memory.ingest_calls] == [
        ("telegram:user:30", "evidence")
    ]
    assert logger.warnings
    assert owner.deleted is False


class ProfileMemory(FakeMemory):
    def __init__(self):
        super().__init__()
        self.profiles = {}

    async def augment(self, *, subject_id, query, scope_id):
        self.augment_calls.append(
            {"subject_id": subject_id, "query": query, "scope_id": scope_id}
        )
        return self.profiles.get(subject_id, "")

    async def revise(self, **payload):
        self.revise_calls.append(payload)
        self.profiles[payload["subject_id"]] = "# User Profile\n\n- Prefers coffee."
        return {"profile_updated": True, "suppressed_count": 0}


class BlockingIngestMemory(FakeMemory):
    def __init__(self):
        super().__init__(augment_value="")
        self.ingest_started = asyncio.Event()
        self.release_ingest = asyncio.Event()

    async def ingest(self, **payload):
        self.ingest_calls.append(payload)
        self.ingest_started.set()
        await self.release_ingest.wait()


@pytest.mark.asyncio
async def test_revised_profile_augments_only_the_target_users_later_request():
    memory = ProfileMemory()
    gateway = FakeGateway(["target answer", "other answer"])
    handler = make_handler(gateway, memory, allowed={20, 30})
    target = FakeMessage("I now prefer coffee", sender_id=20)
    command = FakeMessage(
        "/ai_memory Correct the preference",
        sender_id=10,
        reply_to=target,
    )
    await handler.handle(command)

    target_request = FakeMessage("/ai what do I prefer?", sender_id=20)
    other_request = FakeMessage("/ai what do I prefer?", sender_id=30)
    await handler.handle(target_request)
    await handler.handle(other_request)

    target_context = [item.text for item in gateway.requests[0].context]
    other_context = [item.text for item in gateway.requests[1].context]
    assert any("Prefers coffee" in item for item in target_context)
    assert all("Prefers coffee" not in item for item in other_context)


@pytest.mark.asyncio
async def test_post_answer_memory_ingest_does_not_hold_delegated_rate_lease():
    memory = BlockingIngestMemory()
    gateway = FakeGateway(["first answer", "second answer"])
    handler = make_handler(gateway, memory, allowed={20})

    first = FakeMessage("/ai first", sender_id=20)
    first_task = asyncio.create_task(handler.handle(first))
    await memory.ingest_started.wait()

    second = FakeMessage("/ai second", sender_id=20)
    second_task = asyncio.create_task(handler.handle(second))
    while len(gateway.requests) < 2:
        await asyncio.sleep(0)
    memory.release_ingest.set()

    assert await asyncio.gather(first_task, second_task) == [True, True]
    assert first.replies[0].text == "first answer"
    assert second.replies[0].text == "second answer"
