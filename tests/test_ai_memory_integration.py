import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from aiohttp import web
from telethon.tl import types as telegram_types

from telefire.ai import (
    AIAnswerMarker,
    AIConversationHandler,
    AIStateRepository,
    AIRateLimiter,
    AIResponder,
    AgentEvent,
    AgentRunRequest,
    MessageIdentity,
    MentionedUser,
    MemoryBackfillRequest,
    MemoryDreamResult,
    MemoryDreamState,
    MemoryScopeState,
    PromptBuilder,
    TelegramMessageIdentityResolver,
    TelegramMessageMentionResolver,
)
from telefire.ai_attachments import AttachmentDescription
from telefire.ai_memory import (
    HindsightMemoryClient,
    MemoryDocumentReceipt,
    MemoryEpisode,
    MemoryEvent,
    MemoryRecall,
    MemoryRetainResult,
    MemoryRevisionResult,
    RecalledMemory,
)


class FakeAnswer:
    next_id = 100

    def __init__(self, text, *, reply_to):
        self.id = self.__class__.next_id
        self.__class__.next_id += 1
        self.initial_text = text
        self.text = text
        self.raw_text = text
        self.sender_id = reply_to.sender_id
        self.chat_id = reply_to.chat_id
        self.reply_to_msg_id = reply_to.id
        self.date = reply_to.date
        self.file = None
        self.is_human = True
        self._reply_to = reply_to
        self.edits = []

    async def edit(self, text, **kwargs):
        self.text = text
        self.raw_text = text
        self.edits.append(text)
        return self

    async def get_reply_message(self):
        return self._reply_to


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
        is_human=True,
    ):
        self.id = self.__class__.next_id
        self.__class__.next_id += 1
        self.raw_text = text
        self.sender_id = sender_id
        self.chat_id = chat_id
        self.reply_to_msg_id = reply_to.id if reply_to else None
        self.date = date or datetime(2026, 7, 11, 12, 0, tzinfo=UTC)
        self.file = file
        self.is_human = is_human
        self._reply_to = reply_to
        self.replies = []
        self.deleted = False

    async def get_reply_message(self):
        return self._reply_to

    async def reply(self, text, **kwargs):
        answer = FakeAnswer(text, reply_to=self)
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


class FailingGateway(FakeGateway):
    async def run(self, request: AgentRunRequest) -> AsyncIterator[AgentEvent]:
        self.requests.append(request)
        yield AgentEvent(type="run_started", session_id="session-failed")
        yield AgentEvent(type="run_failed", code="PROVIDER_ERROR", message="failed")


class FakeStore:
    def __init__(self, allowed=()):
        self.allowed = set(allowed)
        self.markers = {}
        self.last_request = {}
        self.memory_documents = {}
        self.memory_continuous = set()
        self.memory_dream = set()
        self.memory_continuous_cursors = {}
        self.memory_excluded = set()
        self.memory_dream_state = {}
        self.memory_labels = {}

    async def get_answer(self, chat_id, answer_message_id):
        return self.markers.get((chat_id, answer_message_id))

    async def get_turn_for_message(self, chat_id, message_id):
        return next(
            (
                marker
                for marker in reversed(tuple(self.markers.values()))
                if marker.chat_id == chat_id
                and message_id
                in {marker.answer_message_id, marker.trigger_message_id}
            ),
            None,
        )

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

    async def get_memory_document_receipt(self, scope_id, document_id):
        return self.memory_documents.get((scope_id, document_id))

    async def save_memory_document_receipt(
        self,
        scope_id,
        document_id,
        content_hash,
        event_versions,
    ):
        self.memory_documents[(scope_id, document_id)] = MemoryDocumentReceipt(
            content_hash,
            event_versions,
        )

    async def find_memory_document_id_for_source(self, scope_id, source_id):
        for (stored_scope_id, document_id), receipt in reversed(
            tuple(self.memory_documents.items())
        ):
            if stored_scope_id != scope_id:
                continue
            if any(
                stored_source_id == source_id
                for stored_source_id, _ in receipt.event_versions
            ):
                return document_id
        return None

    async def record_memory_labels(
        self,
        scope_id,
        scope_display_name,
        actor_labels,
    ):
        self.memory_labels[scope_id] = {
            "scope": scope_display_name,
            "actors": dict(actor_labels),
        }

    async def get_memory_scope_state(self, scope_id):
        return MemoryScopeState(
            scope_id=scope_id,
            continuous_enabled=scope_id in self.memory_continuous,
            dream_enabled=scope_id in self.memory_dream,
            continuous_cursor_message_id=self.memory_continuous_cursors.get(scope_id),
        )

    async def set_continuous_memory_enabled(
        self,
        scope_id,
        enabled,
        display_name=None,
        cursor_message_id=None,
    ):
        if enabled:
            self.memory_continuous.add(scope_id)
            self.memory_continuous_cursors.setdefault(scope_id, cursor_message_id)
        else:
            self.memory_continuous.discard(scope_id)

    async def set_dream_memory_enabled(
        self,
        scope_id,
        enabled,
        display_name=None,
    ):
        if enabled:
            self.memory_dream.add(scope_id)
        else:
            self.memory_dream.discard(scope_id)

    async def mark_memory_excluded_message(self, chat_id, message_id, kind):
        self.memory_excluded.add((chat_id, message_id, kind))

    async def is_memory_excluded_message(self, chat_id, message_id):
        return any(item[:2] == (chat_id, message_id) for item in self.memory_excluded)

    async def get_memory_dream_state(self, scope_id):
        return self.memory_dream_state.get(scope_id, MemoryDreamState(scope_id))


def recalled(text="The group selected PostgreSQL."):
    return MemoryRecall(
        scope_id="telegram:chat:-1001",
        memories=(
            RecalledMemory(
                memory_id="memory-1",
                text=text,
                memory_type="world",
                entities=("telegram:user:20",),
                occurred_start="2026-07-10T08:00:00Z",
                occurred_end="2026-07-10T08:00:00Z",
                mentioned_at="2026-07-10T08:00:00Z",
                document_id="telegram:thread:-1001:1",
                chunk_id="chunk-1",
            ),
        ),
    )


class FakeMemory:
    def __init__(
        self,
        *,
        recall_error=None,
        recall_result=None,
        retain_error=None,
        revise_error=None,
    ):
        self.recall_error = recall_error
        self.recall_result = recall_result if recall_result is not None else recalled()
        self.retain_error = retain_error
        self.revise_error = revise_error
        self.recall_calls = []
        self.retain_calls = []
        self.revise_calls = []

    async def recall(self, *, scope_id, query):
        self.recall_calls.append({"scope_id": scope_id, "query": query})
        if self.recall_error:
            raise self.recall_error
        return self.recall_result

    async def retain(self, episode, *, update_mode="replace"):
        self.retain_calls.append({"episode": episode, "update_mode": update_mode})
        if self.retain_error:
            raise self.retain_error
        return MemoryRetainResult(accepted=True)

    async def retain_many(self, episodes, *, update_mode="replace"):
        for episode in episodes:
            await self.retain(episode, update_mode=update_mode)
        return MemoryRetainResult(accepted=True, items_count=len(episodes))

    async def revise(self, **payload):
        self.revise_calls.append(payload)
        if self.revise_error:
            raise self.revise_error
        return MemoryRevisionResult(invalidated_count=1)


class BlockingRetainMemory(FakeMemory):
    def __init__(self):
        super().__init__(recall_result=MemoryRecall("telegram:chat:-1001", ()))
        self.retain_started = asyncio.Event()
        self.release_retain = asyncio.Event()

    async def retain(self, episode, *, update_mode="replace"):
        self.retain_calls.append({"episode": episode, "update_mode": update_mode})
        self.retain_started.set()
        await self.release_retain.wait()
        return MemoryRetainResult(accepted=True)

    async def retain_many(self, episodes, *, update_mode="replace"):
        for episode in episodes:
            self.retain_calls.append({"episode": episode, "update_mode": update_mode})
        self.retain_started.set()
        await self.release_retain.wait()
        return MemoryRetainResult(accepted=True, items_count=len(episodes))


class FakeLogger:
    def __init__(self):
        self.warnings = []

    def warning(self, message, *args):
        self.warnings.append((message, args))


class FakeAttachmentDescriber:
    def __init__(self, descriptions=None):
        self.descriptions = descriptions or {}

    async def describe(self, message):
        return self.descriptions.get(message.id)


class FakeIdentityResolver:
    async def resolve(self, message):
        return MessageIdentity(
            subject_display_name=f"User {message.sender_id}",
            scope_display_name="Engineering Group",
            is_human=message.is_human,
        )


class FakeMentionResolver:
    async def resolve(self, message):
        return tuple(
            MentionedUser(entity.user_id, f"User {entity.user_id}")
            for entity in getattr(message, "entities", ())
            if getattr(entity, "user_id", None)
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


class FakeBroadcastChannelMessage:
    async def get_sender(self):
        return telegram_types.Channel(
            id=2064685671,
            title="Seele Leaks",
            photo=telegram_types.ChatPhotoEmpty(),
            date=None,
            broadcast=True,
        )

    async def get_chat(self):
        return telegram_types.Channel(
            id=2064685671,
            title="Seele Leaks",
            photo=telegram_types.ChatPhotoEmpty(),
            date=None,
            broadcast=True,
        )


class FakeMentionClient:
    def __init__(self):
        self.entities = {
            "@alice": telegram_types.User(id=40, first_name="Same Name"),
            "@other": telegram_types.User(id=41, first_name="Same Name"),
            42: telegram_types.User(id=42, first_name="Renamed User"),
        }

    async def get_entity(self, candidate):
        if candidate not in self.entities:
            raise ValueError("unknown Telegram entity")
        return self.entities[candidate]


def make_handler(
    gateway,
    memory,
    *,
    allowed=(),
    logger=None,
    attachment_describer=None,
    identity_resolver=None,
    mention_resolver=None,
    history_source=None,
    store=None,
    dream_runner=None,
    memory_command_delete_delay=0,
):
    store = store or FakeStore(allowed=allowed)
    return AIConversationHandler(
        owner_id=10,
        responder=AIResponder(gateway, edit_cadence=0),
        store=store,
        prompt_builder=PromptBuilder(
            attachment_describer=attachment_describer,
            identity_resolver=identity_resolver,
            mention_resolver=mention_resolver,
            history_source=history_source,
        ),
        rate_limiter=AIRateLimiter(store, cooldown_seconds=0),
        memory=memory,
        dream_runner=dream_runner,
        memory_command_delete_delay=memory_command_delete_delay,
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
        subject_id="telegram:user:20",
        subject_display_name="Alice Example",
        scope_display_name="Engineering Group",
        is_human=True,
    )


@pytest.mark.asyncio
async def test_telegram_identity_resolver_accepts_broadcast_channel_posts():
    identity = await TelegramMessageIdentityResolver().resolve(
        FakeBroadcastChannelMessage()
    )

    assert identity == MessageIdentity(
        subject_id="telegram:channel:2064685671",
        subject_display_name="Seele Leaks",
        scope_display_name="Seele Leaks",
        is_human=False,
    )
    assert identity.is_memory_source


@pytest.mark.asyncio
async def test_telegram_identity_resolver_rejects_bots_and_unresolved_senders():
    class BotMessage(FakeTelegramIdentityMessage):
        async def get_sender(self):
            return telegram_types.User(id=21, first_name="Helper", bot=True)

    class UnresolvedMessage(FakeTelegramIdentityMessage):
        async def get_sender(self):
            raise ValueError("sender unavailable")

    assert not (await TelegramMessageIdentityResolver().resolve(BotMessage())).is_human
    assert not (
        await TelegramMessageIdentityResolver().resolve(UnresolvedMessage())
    ).is_human


@pytest.mark.asyncio
async def test_telegram_mention_resolver_trusts_entities_and_keeps_stable_users():
    client = FakeMentionClient()
    resolver = TelegramMessageMentionResolver(client)
    message = FakeMessage("@alice and @other plus @ghost", sender_id=10)
    message.entities = (
        telegram_types.MessageEntityMention(offset=0, length=6),
        telegram_types.MessageEntityMention(offset=11, length=6),
        telegram_types.MessageEntityMentionName(offset=23, length=6, user_id=42),
    )

    mentions = await resolver.resolve(message)
    assert mentions == (
        MentionedUser(40, "Same Name"),
        MentionedUser(41, "Same Name"),
        MentionedUser(42, "Renamed User"),
    )

    client.entities[42] = telegram_types.User(id=42, first_name="Newest Name")
    assert await resolver.resolve(message) == (
        MentionedUser(40, "Same Name"),
        MentionedUser(41, "Same Name"),
        MentionedUser(42, "Newest Name"),
    )

    plain = FakeMessage("plain @alice text", sender_id=10)
    plain.entities = ()
    assert await resolver.resolve(plain) == ()


@pytest.mark.asyncio
async def test_bare_memory_command_retains_one_ordered_multi_actor_episode():
    store = FakeStore()
    memory = FakeMemory(recall_result=MemoryRecall("telegram:chat:-1001", ()))
    handler = make_handler(
        FakeGateway(["unused"]),
        memory,
        store=store,
        identity_resolver=FakeIdentityResolver(),
        mention_resolver=FakeMentionResolver(),
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
    target.entities = (SimpleNamespace(user_id=40),)
    command = FakeMessage("/ai_memory", sender_id=10, reply_to=target)

    assert await handler.handle(command) is True
    assert command.replies[0].text == "Memory stored from reply chain: 2 messages."
    assert command.deleted is True
    assert len(memory.retain_calls) == 1
    call = memory.retain_calls[0]
    assert call["update_mode"] == "replace"
    item = call["episode"]
    assert item.scope_id == "telegram:chat:-1001"
    assert item.scope_display_name == "Engineering Group"
    assert item.document_id == f"telegram:thread:-1001:{ancestor.id}"
    assert [(event.actor_id, event.text) for event in item.events] == [
        ("telegram:user:30", "I use Python at work"),
        ("telegram:user:20", "I started using Rust today"),
    ]
    assert item.events[1].reply_to_source_id.endswith(f":{ancestor.id}")
    assert item.events[1].mentioned_actors == (("telegram:user:40", "User 40"),)
    assert "telegram:user:40" in item.actor_ids
    receipt = store.memory_documents[(item.scope_id, item.document_id)]
    assert receipt.content_hash == item.content_hash
    assert receipt.event_versions == item.event_versions


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "document_id",
    (
        "telegram:dream-segment:-1001:0-19",
        "telegram:dream-session:-1001:20260713T080000Z:1",
    ),
)
async def test_memory_command_appends_to_existing_dream_document(
    tmp_path,
    document_id,
):
    store = FakeStore()
    memory = FakeMemory()
    handler = make_handler(
        FakeGateway(["unused"]),
        memory,
        store=store,
        identity_resolver=FakeIdentityResolver(),
    )
    root = FakeMessage("Root evidence", sender_id=20)
    target = FakeMessage("New reply evidence", sender_id=30, reply_to=root)
    existing = MemoryEpisode(
        scope_id="telegram:chat:-1001",
        document_id=document_id,
        events=(
            MemoryEvent(
                source_id=f"telegram:message:-1001:{root.id}",
                actor_id="telegram:user:20",
                actor_display_name="User 20",
                occurred_at=root.date,
                mentioned_at=root.date,
                text=root.raw_text,
            ),
        ),
    )
    store.memory_documents[(existing.scope_id, document_id)] = MemoryDocumentReceipt(
        existing.content_hash,
        existing.event_versions,
    )
    command = FakeMessage("/ai_memory", sender_id=10, reply_to=target)

    assert await handler.handle(command) is True

    call = memory.retain_calls[0]
    assert call["update_mode"] == "append"
    assert call["episode"].document_id == document_id
    assert [event.source_id for event in call["episode"].events] == [
        f"telegram:message:-1001:{root.id}",
        f"telegram:message:-1001:{target.id}",
    ]
    receipt = store.memory_documents[(existing.scope_id, document_id)]
    assert len(receipt.event_versions) == 2


@pytest.mark.asyncio
async def test_exact_memory_retry_uses_delivery_receipt_without_second_retain():
    store = FakeStore()
    memory = FakeMemory()
    handler = make_handler(FakeGateway(["unused"]), memory, store=store)
    target = FakeMessage("I prefer tea", sender_id=20)

    first = FakeMessage("/ai_memory", sender_id=10, reply_to=target)
    second = FakeMessage("/ai_memory", sender_id=10, reply_to=target)
    assert await handler.handle(first) is True
    assert await handler.handle(second) is True

    assert first.replies[0].text == "Memory stored from reply chain: 1 message."
    assert second.replies[0].text == "Already remembered."
    assert len(memory.retain_calls) == 1


@pytest.mark.asyncio
async def test_ai_request_delegates_scope_and_identity_anchors_to_agent():
    ancestor = FakeMessage("I use PostgreSQL at work", sender_id=20)
    trigger = FakeMessage(
        "/ai which database should we use?",
        sender_id=10,
        reply_to=ancestor,
    )
    memory = FakeMemory()
    gateway = FakeGateway(["Use PostgreSQL"])
    handler = make_handler(
        gateway,
        memory,
        identity_resolver=FakeIdentityResolver(),
    )

    assert await handler.handle(trigger) is True

    assert memory.recall_calls == []
    request = gateway.requests[0]
    assert [item.kind for item in request.context] == ["reference"]
    assert "Untrusted chat context" in request.context[0].text
    assert request.memory is not None
    assert request.memory.scope_id == "telegram:chat:-1001"
    assert [(item.identity, item.label) for item in request.memory.anchors] == [
        ("telegram:user:10", "User 10"),
        ("telegram:user:20", "User 20"),
    ]
    assert len(memory.retain_calls) == 1


@pytest.mark.asyncio
async def test_recent_chat_participants_become_memory_identity_anchors():
    recent = FakeMessage("Alice prefers local models", sender_id=20)

    class HistorySource:
        async def fetch_recent(self, trigger, *, limit):
            assert limit == 1
            return (recent,)

    memory = FakeMemory()
    gateway = FakeGateway(["Use a local model"])
    trigger = FakeMessage("/ai1 what does Alice prefer?", sender_id=10)
    handler = make_handler(
        gateway,
        memory,
        identity_resolver=FakeIdentityResolver(),
        history_source=HistorySource(),
    )

    assert await handler.handle(trigger) is True

    target = gateway.requests[0].memory
    assert target is not None
    assert [(item.identity, item.label) for item in target.anchors] == [
        ("telegram:user:10", "User 10"),
        ("telegram:user:20", "User 20"),
    ]


@pytest.mark.asyncio
async def test_recent_only_context_is_not_automatically_retained():
    recent = FakeMessage("Alice prefers local models", sender_id=20)

    class HistorySource:
        async def fetch_recent(self, trigger, *, limit):
            return (recent,)

    store = FakeStore()
    memory = FakeMemory()
    trigger = FakeMessage("/ai1 what does Alice prefer?", sender_id=10)
    handler = make_handler(
        FakeGateway(["Use a local model"]),
        memory,
        store=store,
        identity_resolver=FakeIdentityResolver(),
        history_source=HistorySource(),
    )

    assert await handler.handle(trigger) is True

    episode = memory.retain_calls[0]["episode"]
    assert [(event.actor_id, event.text) for event in episode.events] == [
        ("telegram:user:10", "what does Alice prefer?")
    ]


@pytest.mark.asyncio
async def test_ai_request_bounds_identity_anchors_for_agent_contract():
    memory = FakeMemory()
    gateway = FakeGateway(["bounded"])
    trigger = FakeMessage("/ai summarize", sender_id=10)
    trigger.entities = tuple(SimpleNamespace(user_id=index) for index in range(40, 110))
    handler = make_handler(gateway, memory, mention_resolver=FakeMentionResolver())

    assert await handler.handle(trigger) is True

    target = gateway.requests[0].memory
    assert target is not None
    assert len(target.anchors) == 64
    assert target.anchors[0].identity == "telegram:user:10"
    assert target.anchors[-1].identity == "telegram:user:102"


@pytest.mark.asyncio
async def test_out_of_chain_exact_mention_enters_agent_anchors_and_episode_entities():
    store = FakeStore()
    memory = FakeMemory()
    trigger = FakeMessage("/ai what does @alice prefer?", sender_id=10)
    trigger.entities = (SimpleNamespace(user_id=40),)
    gateway = FakeGateway(["answer"])
    handler = make_handler(
        gateway,
        memory,
        store=store,
        identity_resolver=FakeIdentityResolver(),
        mention_resolver=FakeMentionResolver(),
    )

    assert await handler.handle(trigger) is True
    target = gateway.requests[0].memory
    assert target is not None
    assert ("telegram:user:40", "User 40") in [
        (item.identity, item.label) for item in target.anchors
    ]
    event = memory.retain_calls[0]["episode"].events[0]
    assert event.mentioned_actors == (("telegram:user:40", "User 40"),)
    assert "telegram:user:40" in memory.retain_calls[0]["episode"].actor_ids


@pytest.mark.asyncio
async def test_ai_generated_chain_message_is_context_but_not_retained_evidence():
    human = FakeMessage("I prefer Rust", sender_id=20)
    ai_output = FakeMessage("Generated claim", sender_id=10, reply_to=human)
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
    handler = make_handler(FakeGateway(["unused"]), memory, store=store)
    command = FakeMessage("/ai_memory", sender_id=10, reply_to=ai_output)

    assert await handler.handle(command) is True
    episode = memory.retain_calls[0]["episode"]
    assert [(event.actor_id, event.text) for event in episode.events] == [
        ("telegram:user:20", "I prefer Rust")
    ]


@pytest.mark.asyncio
async def test_attachment_description_is_retained_without_raw_attachment():
    target = FakeMessage("", sender_id=20, file=object())
    description = AttachmentDescription(
        context_text="Generated attachment context: architecture diagram",
        memory_text=(
            "The subject shared an attachment. Generated content description: "
            "architecture diagram"
        ),
    )
    memory = FakeMemory()
    handler = make_handler(
        FakeGateway(["unused"]),
        memory,
        attachment_describer=FakeAttachmentDescriber({target.id: description}),
    )
    command = FakeMessage("/ai_memory", sender_id=10, reply_to=target)

    assert await handler.handle(command) is True
    event = memory.retain_calls[0]["episode"].events[0]
    assert "architecture diagram" in event.text
    assert "telegram" not in event.text.lower()


@pytest.mark.asyncio
async def test_episode_preserves_quote_and_forward_provenance_without_raw_media():
    target = FakeMessage(
        "Carol said the migration moved.",
        sender_id=20,
        date=datetime(2026, 7, 12, 10, 0, tzinfo=UTC),
    )
    target.reply_to = SimpleNamespace(
        quote_text="The migration starts Monday.",
        quote_offset=6,
    )
    target.reply_to_msg_id = 77
    target.fwd_from = SimpleNamespace(
        from_name="Carol Example",
        from_id=telegram_types.PeerUser(user_id=55),
        saved_from_peer=telegram_types.PeerChannel(channel_id=1001),
        saved_from_msg_id=99,
        channel_post=None,
        date=datetime(2026, 7, 10, 9, 0, tzinfo=UTC),
    )
    memory = FakeMemory()
    handler = make_handler(FakeGateway(["unused"]), memory)

    command = FakeMessage("/ai_memory", sender_id=10, reply_to=target)
    assert await handler.handle(command) is True

    event = memory.retain_calls[0]["episode"].events[0]
    assert event.mentioned_at == datetime(2026, 7, 12, 10, 0, tzinfo=UTC)
    assert event.metadata["quotation"] == {
        "text": "The migration starts Monday.",
        "source_id": "telegram:message:-1001:77",
        "offset": 6,
    }
    assert event.metadata["forwarded_from"] == {
        "actor_display_name": "Carol Example",
        "actor_id": "telegram:user:55",
        "source_id": "telegram:message:-1000000001001:99",
        "source_time": "2026-07-10T09:00:00Z",
    }


@pytest.mark.parametrize(
    "failure",
    [TimeoutError(), ConnectionError(), ValueError("malformed")],
)
@pytest.mark.asyncio
async def test_telefire_does_not_call_memory_recall(failure):
    logger = FakeLogger()
    memory = FakeMemory(recall_error=failure)
    gateway = FakeGateway(["answer without memory"])
    handler = make_handler(gateway, memory, logger=logger)
    trigger = FakeMessage("/ai answer me", sender_id=10)

    assert await handler.handle(trigger) is True
    assert trigger.replies[0].text == "answer without memory"
    assert gateway.requests[0].context == ()
    assert gateway.requests[0].memory is not None
    assert memory.recall_calls == []
    assert logger.warnings == []


@pytest.mark.asyncio
async def test_memory_retain_failure_does_not_store_delivery_receipt():
    logger = FakeLogger()
    store = FakeStore()
    memory = FakeMemory(retain_error=ConnectionError())
    handler = make_handler(FakeGateway(["unused"]), memory, store=store, logger=logger)
    target = FakeMessage("I prefer tea", sender_id=20)
    command = FakeMessage("/ai_memory", sender_id=10, reply_to=target)

    assert await handler.handle(command) is True
    assert command.replies[0].text == "Memory update failed. Retry the command."
    assert command.deleted is True
    assert store.memory_documents == {}
    assert logger.warnings


@pytest.mark.asyncio
async def test_owner_revision_retains_chain_then_revises_direct_human():
    memory = FakeMemory()
    handler = make_handler(FakeGateway(["unused"]), memory)
    ancestor = FakeMessage("I use Python", sender_id=30)
    target = FakeMessage("I prefer tea", sender_id=20, reply_to=ancestor)
    command = FakeMessage(
        "/ai_memory Correct the preference to coffee",
        sender_id=10,
        reply_to=target,
    )

    assert await handler.handle(command) is True
    assert command.replies[0].text == "Memory updated."
    assert len(memory.retain_calls) == 2
    revision = memory.retain_calls[1]["episode"]
    assert revision.document_id == f"telegram:revision:-1001:{command.id}"
    assert revision.source == "telegram-revision"
    assert revision.events[0].mentioned_actors == (("telegram:user:20", None),)
    assert "Correct the preference to coffee" in revision.events[0].text
    assert memory.revise_calls == [
        {
            "scope_id": "telegram:chat:-1001",
            "subject_id": "telegram:user:20",
            "instruction": "Correct the preference to coffee",
        }
    ]
    assert command.deleted is True


@pytest.mark.asyncio
async def test_revision_does_not_curate_when_correction_evidence_fails():
    class RevisionEvidenceFailure(FakeMemory):
        async def retain_many(self, episodes, *, update_mode="replace"):
            if any(episode.source == "telegram-revision" for episode in episodes):
                raise ConnectionError("revision evidence unavailable")
            return await super().retain_many(episodes, update_mode=update_mode)

    memory = RevisionEvidenceFailure()
    handler = make_handler(FakeGateway(["unused"]), memory)
    target = FakeMessage("I prefer tea", sender_id=20)
    command = FakeMessage(
        "/ai_memory Correct the preference to coffee",
        sender_id=10,
        reply_to=target,
    )

    assert await handler.handle(command) is True
    assert command.replies[0].text == "Memory revision failed. Retry the command."
    assert command.deleted is True
    assert memory.revise_calls == []


@pytest.mark.asyncio
async def test_revision_requires_direct_human_target_and_owner():
    memory = FakeMemory()
    store = FakeStore(allowed={20})
    human = FakeMessage("I prefer Rust", sender_id=20)
    ai_output = FakeMessage("Generated answer", sender_id=10, reply_to=human)
    store.markers[(ai_output.chat_id, ai_output.id)] = AIAnswerMarker(
        chat_id=ai_output.chat_id,
        answer_message_id=ai_output.id,
        trigger_message_id=999,
        requester_id=20,
        prompt="question",
        answer_text=ai_output.raw_text,
        parent_answer_message_id=None,
        reference_context="",
        agent_session_id="session-old",
        agent_entry_id="entry-old",
    )
    handler = make_handler(FakeGateway(["unused"]), memory, store=store)

    unauthorized = FakeMessage("/ai_memory change memory", sender_id=20, reply_to=human)
    invalid_target = FakeMessage(
        "/ai_memory change memory", sender_id=10, reply_to=ai_output
    )
    assert await handler.handle(unauthorized) is False
    assert await handler.handle(invalid_target) is True
    assert invalid_target.replies[0].text == (
        "Reply directly to a human message when revising memory."
    )
    assert unauthorized.deleted is False
    assert invalid_target.deleted is False
    assert memory.retain_calls == []
    assert memory.revise_calls == []


@pytest.mark.asyncio
async def test_revision_rejects_a_telegram_bot_target():
    memory = FakeMemory()
    target = FakeMessage("Automated claim", sender_id=50, is_human=False)
    command = FakeMessage(
        "/ai_memory change memory",
        sender_id=10,
        reply_to=target,
    )
    handler = make_handler(
        FakeGateway(["unused"]),
        memory,
        identity_resolver=FakeIdentityResolver(),
    )

    assert await handler.handle(command) is True
    assert command.replies[0].text == (
        "Reply directly to a human message when revising memory."
    )
    assert command.deleted is False
    assert memory.retain_calls == []
    assert memory.revise_calls == []


@pytest.mark.asyncio
async def test_bare_memory_command_without_reply_remains_visible_with_usage():
    handler = make_handler(FakeGateway(["unused"]), FakeMemory())
    command = FakeMessage("/ai_memory", sender_id=10)

    assert await handler.handle(command) is True
    assert command.replies[0].text == (
        "Usage: reply to a user with /ai_memory [instruction]"
    )
    assert command.deleted is False


@pytest.mark.asyncio
async def test_saved_memory_entrypoint_retains_source_and_ancestors():
    memory = FakeMemory()
    handler = make_handler(
        FakeGateway(["unused"]),
        memory,
        identity_resolver=FakeIdentityResolver(),
    )
    ancestor = FakeMessage("I use Python at work", sender_id=30)
    source = FakeMessage("I started using Rust today", sender_id=20, reply_to=ancestor)

    assert await handler.remember_reply_chain(source) is True
    episode = memory.retain_calls[0]["episode"]
    assert [event.text for event in episode.events] == [
        "I use Python at work",
        "I started using Rust today",
    ]


@pytest.mark.asyncio
async def test_saved_memory_entrypoint_rejects_non_human_source():
    memory = FakeMemory()
    handler = make_handler(
        FakeGateway(["unused"]),
        memory,
        identity_resolver=FakeIdentityResolver(),
    )
    source = FakeMessage("Automated channel post", sender_id=50, is_human=False)

    assert await handler.remember_reply_chain(source) is False
    assert memory.retain_calls == []


@pytest.mark.asyncio
async def test_telegram_handler_retains_through_hindsight_and_delegates_recall():
    received = {"profiles": [], "retain": [], "recall": []}

    async def upsert_bank(request):
        payload = await request.json()
        received["profiles"].append(payload)
        return web.json_response(
            {
                "bank_id": request.match_info["bank_id"],
                "name": payload["name"],
                "disposition": {"empathy": 3, "literalism": 3, "skepticism": 3},
                "mission": "",
            }
        )

    async def retain(request):
        received["retain"].append(await request.json())
        return web.json_response(
            {
                "success": True,
                "bank_id": request.match_info["bank_id"],
                "items_count": 1,
                "async": False,
            }
        )

    async def recall(request):
        received["recall"].append(await request.json())
        return web.json_response(
            {
                "results": [
                    {
                        "id": "memory-db",
                        "text": "User 20 uses PostgreSQL at work.",
                        "type": "world",
                        "entities": ["telegram:user:20"],
                        "document_id": "telegram:thread:-1001:1",
                        "chunk_id": "chunk-db",
                    }
                ]
            }
        )

    app = web.Application()
    app.router.add_put("/v1/default/banks/{bank_id}", upsert_bank)
    app.router.add_post("/v1/default/banks/{bank_id}/memories", retain)
    app.router.add_post("/v1/default/banks/{bank_id}/memories/recall", recall)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    client = HindsightMemoryClient(f"http://127.0.0.1:{port}")
    gateway = FakeGateway(["Use PostgreSQL"])
    store = FakeStore()
    handler = make_handler(
        gateway,
        client,
        store=store,
        identity_resolver=FakeIdentityResolver(),
    )
    source = FakeMessage("I use PostgreSQL at work", sender_id=20)
    remember = FakeMessage("/ai_memory", sender_id=10, reply_to=source)
    ask = FakeMessage("/ai which database should we use?", sender_id=10)
    try:
        assert await handler.handle(remember) is True
        assert await handler.handle(ask) is True

        assert received["profiles"] == [{"name": "Engineering Group"}]
        assert len(received["retain"]) == 2
        retained = received["retain"][0]["items"][0]
        assert retained["document_id"] == f"telegram:thread:-1001:{source.id}"
        assert retained["entities"] == [{"text": "telegram:user:20", "type": "PERSON"}]
        assert received["recall"] == []
        target = gateway.requests[0].memory
        assert target is not None
        assert target.scope_id == "telegram:chat:-1001"
    finally:
        await client.close()
        await runner.cleanup()


@pytest.mark.asyncio
async def test_owner_controls_continuous_and_dream_memory_independently():
    store = FakeStore()
    memory = FakeMemory(recall_result=MemoryRecall("telegram:chat:-1001", ()))
    gateway = FakeGateway(["answer"])
    handler = make_handler(
        gateway,
        memory,
        store=store,
        identity_resolver=FakeIdentityResolver(),
    )

    enable = FakeMessage("/ai_memory_enable", sender_id=10)
    assert await handler.handle(enable) is True
    assert enable.deleted is True
    assert enable.replies[0].text == (
        "Continuous memory enabled for this chat. New messages will be remembered."
    )

    status = FakeMessage("/ai_memory_status", sender_id=10)
    assert await handler.handle(status) is True
    assert status.deleted is True
    assert status.replies[0].text == (
        "Continuous memory: enabled\n"
        "Dream: disabled\n"
        f"Continuous cursor: {enable.id}\n"
        "Last Dream attempt: never\n"
        "Last Dream success: never\n"
        "Last Dream error: none"
    )

    dream_enable = FakeMessage("/ai_dream_enable", sender_id=10)
    assert await handler.handle(dream_enable) is True
    assert dream_enable.deleted is True
    assert dream_enable.replies[0].text == (
        "Dream enabled for this chat, but continuous memory currently overrides it."
    )

    store.memory_dream_state["telegram:chat:-1001"] = MemoryDreamState(
        scope_id="telegram:chat:-1001",
        cursor_message_id=44,
        last_attempt_at=1,
        last_success_at=2,
        last_error="temporary backpressure",
    )
    populated_status = FakeMessage("/ai_memory_status", sender_id=10)
    assert await handler.handle(populated_status) is True
    assert "Last Dream attempt: 1970-01-01T00:00:01Z" in (
        populated_status.replies[0].text
    )
    assert "Last Dream success: 1970-01-01T00:00:02Z" in (
        populated_status.replies[0].text
    )
    assert populated_status.replies[0].text.endswith(
        "Last Dream error: temporary backpressure"
    )

    disable = FakeMessage("/ai_memory_disable", sender_id=10)
    assert await handler.handle(disable) is True
    assert disable.deleted is True
    assert disable.replies[0].text == (
        "Continuous memory disabled for this chat. Dream remains enabled."
    )


@pytest.mark.asyncio
async def test_enabled_continuation_retains_human_chain_across_ai_answer():
    store = FakeStore()
    memory = FakeMemory(recall_result=MemoryRecall("telegram:chat:-1001", ()))
    gateway = FakeGateway(["first answer", "follow-up answer"])
    handler = make_handler(
        gateway,
        memory,
        store=store,
        identity_resolver=FakeIdentityResolver(),
    )

    ancestor = FakeMessage("The Transformer code is trivial", sender_id=20)
    trigger = FakeMessage(
        "/ai what do they mean?",
        sender_id=10,
        reply_to=ancestor,
    )
    assert await handler.handle(trigger) is True

    follow_up = FakeMessage(
        "They meant your implementation",
        sender_id=10,
        reply_to=trigger.replies[0],
    )
    assert await handler.handle(follow_up) is True

    assert len(memory.retain_calls) == 2
    episode = memory.retain_calls[1]["episode"]
    assert episode.document_id == f"telegram:thread:-1001:{ancestor.id}"
    assert [(event.actor_id, event.text) for event in episode.events] == [
        ("telegram:user:20", "The Transformer code is trivial"),
        ("telegram:user:10", "what do they mean?"),
        ("telegram:user:10", "They meant your implementation"),
    ]
    assert any(
        item.identity == "telegram:user:20"
        for item in gateway.requests[1].memory.anchors
    )
    assert not any(item.kind == "reference" for item in gateway.requests[1].context)


@pytest.mark.asyncio
async def test_ai_retains_reply_chain_without_scope_enablement():
    memory = FakeMemory()
    gateway = FakeGateway(["answer"])
    handler = make_handler(gateway, memory, store=FakeStore())
    ancestor = FakeMessage("I use PostgreSQL", sender_id=20)
    trigger = FakeMessage("/ai answer from memory", sender_id=10, reply_to=ancestor)

    assert await handler.handle(trigger) is True
    assert gateway.requests[0].memory is not None
    assert memory.recall_calls == []
    assert len(memory.retain_calls) == 1
    assert [(event.actor_id, event.text) for event in memory.retain_calls[0][
        "episode"
    ].events] == [
        ("telegram:user:20", "I use PostgreSQL"),
        ("telegram:user:10", "answer from memory"),
    ]


@pytest.mark.asyncio
async def test_continuous_scope_skips_duplicate_ai_reply_chain_retain():
    store = FakeStore()
    store.memory_continuous.add("telegram:chat:-1001")
    memory = FakeMemory()
    gateway = FakeGateway(["answer"])
    handler = make_handler(gateway, memory, store=store)
    ancestor = FakeMessage("I use PostgreSQL", sender_id=20)
    trigger = FakeMessage("/ai answer from memory", sender_id=10, reply_to=ancestor)

    assert await handler.handle(trigger) is True
    assert gateway.requests[0].memory is not None
    assert memory.retain_calls == []


@pytest.mark.asyncio
async def test_enabled_scope_does_not_retain_non_human_ai_request():
    store = FakeStore(allowed={20})
    memory = FakeMemory()
    handler = make_handler(
        FakeGateway(["answer"]),
        memory,
        store=store,
        identity_resolver=FakeIdentityResolver(),
    )
    trigger = FakeMessage("/ai calculate", sender_id=20, is_human=False)

    assert await handler.handle(trigger) is True
    assert memory.retain_calls == []


@pytest.mark.asyncio
async def test_failed_agent_run_does_not_retain_enabled_scope():
    store = FakeStore()
    memory = FakeMemory()
    handler = make_handler(FailingGateway([]), memory, store=store)
    trigger = FakeMessage("/ai fail", sender_id=10)

    assert await handler.handle(trigger) is True
    assert trigger.replies[0].text == "AI request failed. Try again later."
    assert memory.retain_calls == []


@pytest.mark.asyncio
async def test_non_owner_cannot_change_scope_memory_state():
    store = FakeStore(allowed={20})
    handler = make_handler(FakeGateway(["unused"]), FakeMemory(), store=store)
    command = FakeMessage("/ai_memory_enable", sender_id=20)

    assert await handler.handle(command) is False
    assert command.replies == []
    assert command.deleted is False
    assert store.memory_continuous == set()
    assert store.memory_dream == set()


@pytest.mark.asyncio
async def test_post_answer_retain_does_not_hold_delegated_rate_lease():
    store = FakeStore(allowed={20})
    memory = BlockingRetainMemory()
    gateway = FakeGateway(["first answer", "second answer"])
    handler = make_handler(gateway, memory, store=store, allowed={20})

    first = FakeMessage("/ai first", sender_id=20)
    first_task = asyncio.create_task(handler.handle(first))
    await memory.retain_started.wait()

    second = FakeMessage("/ai second", sender_id=20)
    second_task = asyncio.create_task(handler.handle(second))
    while len(gateway.requests) < 2:
        await asyncio.sleep(0)
    memory.release_retain.set()

    assert await asyncio.gather(first_task, second_task) == [True, True]
    assert first.replies[0].text == "first answer"
    assert second.replies[0].text == "second answer"


@pytest.mark.asyncio
async def test_memory_scope_modes_persist_across_repository_restart(tmp_path):
    state_path = tmp_path / "ai.db"
    first = await AIStateRepository(state_path).connect()
    await first.set_continuous_memory_enabled(
        "telegram:chat:-1001",
        True,
        "Engineering Group",
        cursor_message_id=41,
    )
    await first.set_dream_memory_enabled("telegram:chat:-1001", True)
    await first.close()

    second = await AIStateRepository(state_path).connect()
    try:
        assert await second.get_memory_scope_state(
            "telegram:chat:-1001"
        ) == MemoryScopeState(
            scope_id="telegram:chat:-1001",
            continuous_enabled=True,
            dream_enabled=True,
            continuous_cursor_message_id=41,
        )
        assert await second.get_memory_scope_state(
            "telegram:chat:-2002"
        ) == MemoryScopeState(scope_id="telegram:chat:-2002")
    finally:
        await second.close()


@pytest.mark.asyncio
async def test_legacy_enabled_scope_migrates_to_dream_only(tmp_path):
    import sqlite3

    state_path = tmp_path / "ai.db"
    connection = sqlite3.connect(state_path)
    connection.execute(
        """
        CREATE TABLE ai_memory_scopes (
            scope_id TEXT PRIMARY KEY,
            enabled INTEGER NOT NULL,
            display_name TEXT,
            updated_at REAL NOT NULL
        )
        """
    )
    connection.execute(
        "INSERT INTO ai_memory_scopes VALUES (?, ?, ?, ?)",
        ("telegram:chat:-1001", 1, "Engineering Group", 1.0),
    )
    connection.commit()
    connection.close()

    store = await AIStateRepository(state_path).connect()
    try:
        assert await store.get_memory_scope_state(
            "telegram:chat:-1001"
        ) == MemoryScopeState(
            scope_id="telegram:chat:-1001",
            continuous_enabled=False,
            dream_enabled=True,
        )
    finally:
        await store.close()


class FakeDreamRunner:
    def __init__(self, error=None):
        self.error = error
        self.calls = []
        self.backfill_calls = []

    async def run_scope(self, chat_id):
        self.calls.append(chat_id)
        if self.error:
            raise self.error
        return MemoryDreamResult(
            messages_seen=4,
            messages_retained=3,
            documents_created=2,
            documents_unchanged=1,
        )

    async def run_backfill(self, chat_id, request):
        self.backfill_calls.append((chat_id, request))
        if self.error:
            raise self.error
        return MemoryDreamResult(
            messages_seen=4,
            messages_retained=3,
            documents_created=2,
            documents_unchanged=1,
        )


@pytest.mark.asyncio
async def test_owner_runs_manual_dream_only_for_dream_scope():
    store = FakeStore()
    runner = FakeDreamRunner()
    handler = make_handler(
        FakeGateway(["unused"]),
        FakeMemory(),
        store=store,
        dream_runner=runner,
    )

    disabled = FakeMessage("/ai_memory_dream", sender_id=10)
    assert await handler.handle(disabled) is True
    assert disabled.replies[0].text == "Dream is disabled for this chat."
    assert disabled.deleted is True
    assert runner.calls == []

    store.memory_dream.add("telegram:chat:-1001")
    enabled = FakeMessage("/ai_memory_dream", sender_id=10)
    assert await handler.handle(enabled) is True
    assert enabled.replies[0].text == (
        "Dream Cycle complete: 3 messages in 2 updated threads; 1 unchanged."
    )
    assert enabled.deleted is True
    assert runner.calls == [-1001]


@pytest.mark.asyncio
async def test_failed_manual_dream_deletes_command_but_keeps_error_visible():
    store = FakeStore()
    store.memory_dream.add("telegram:chat:-1001")
    runner = FakeDreamRunner(error=ConnectionError("memory unavailable"))
    handler = make_handler(
        FakeGateway(["unused"]),
        FakeMemory(),
        store=store,
        dream_runner=runner,
    )
    command = FakeMessage("/ai_memory_dream", sender_id=10)

    assert await handler.handle(command) is True
    assert command.replies[0].text == (
        "Dream Cycle failed. It will retry from the previous cursor."
    )
    assert command.deleted is True


@pytest.mark.parametrize(
    ("command_text", "expected_request", "progress"),
    [
        (
            "/ai_memory_backfill days 7",
            MemoryBackfillRequest(mode="days", value=7),
            "Backfilling the last 7 days...",
        ),
        (
            "/ai_memory_backfill messages 500",
            MemoryBackfillRequest(mode="messages", value=500),
            "Backfilling the latest 500 messages...",
        ),
    ],
)
@pytest.mark.asyncio
async def test_owner_runs_backfill_while_automatic_memory_is_disabled(
    command_text,
    expected_request,
    progress,
):
    store = FakeStore()
    runner = FakeDreamRunner()
    handler = make_handler(
        FakeGateway(["unused"]),
        FakeMemory(),
        store=store,
        dream_runner=runner,
    )
    command = FakeMessage(command_text, sender_id=10)

    assert await handler.handle(command) is True
    assert runner.backfill_calls == [(-1001, expected_request)]
    assert command.replies[0].edits == [
        "Memory backfill complete: scanned 4 messages; retained 3 in "
        "2 updated threads; 1 unchanged."
    ]
    assert command.replies[0].initial_text == progress
    assert command.deleted is True


@pytest.mark.asyncio
async def test_invalid_and_non_owner_backfill_commands_do_not_start_work():
    runner = FakeDreamRunner()
    handler = make_handler(
        FakeGateway(["unused"]),
        FakeMemory(),
        store=FakeStore(allowed={20}),
        dream_runner=runner,
    )
    invalid = FakeMessage("/ai_memory_backfill days 31", sender_id=10)
    non_owner = FakeMessage("/ai_memory_backfill days 7", sender_id=20)

    assert await handler.handle(invalid) is True
    assert invalid.replies[0].text == (
        "Usage: /ai_memory_backfill days <1-30> or "
        "/ai_memory_backfill messages <1-5000>"
    )
    assert invalid.deleted is False
    assert await handler.handle(non_owner) is False
    assert non_owner.replies == []
    assert runner.backfill_calls == []


@pytest.mark.asyncio
async def test_failed_backfill_deletes_command_and_edits_progress_with_error():
    runner = FakeDreamRunner(error=ConnectionError("memory unavailable"))
    handler = make_handler(
        FakeGateway(["unused"]),
        FakeMemory(),
        store=FakeStore(),
        dream_runner=runner,
    )
    command = FakeMessage("/ai_memory_backfill messages 50", sender_id=10)

    assert await handler.handle(command) is True
    assert command.replies[0].edits == [
        "Memory backfill failed. Accepted documents are safe; retry the same command."
    ]
    assert command.deleted is True


@pytest.mark.asyncio
async def test_memory_status_command_is_deleted_after_configured_delay():
    handler = make_handler(
        FakeGateway(["unused"]),
        FakeMemory(),
        store=FakeStore(),
        memory_command_delete_delay=0.01,
    )
    command = FakeMessage("/ai_memory_status", sender_id=10)

    assert await handler.handle(command) is True
    assert command.deleted is False
    assert command.replies[0].text.startswith("Continuous memory: disabled")

    await asyncio.sleep(0.02)

    assert command.deleted is True


@pytest.mark.asyncio
async def test_memory_dream_command_deletes_while_scan_is_still_running():
    class BlockingDreamRunner(FakeDreamRunner):
        def __init__(self):
            super().__init__()
            self.started = asyncio.Event()
            self.release = asyncio.Event()

        async def run_scope(self, chat_id):
            self.calls.append(chat_id)
            self.started.set()
            await self.release.wait()
            return MemoryDreamResult(
                messages_seen=1,
                messages_retained=1,
                documents_created=1,
                documents_unchanged=0,
            )

    store = FakeStore()
    store.memory_dream.add("telegram:chat:-1001")
    runner = BlockingDreamRunner()
    handler = make_handler(
        FakeGateway(["unused"]),
        FakeMemory(),
        store=store,
        dream_runner=runner,
        memory_command_delete_delay=0.01,
    )
    command = FakeMessage("/ai_memory_dream", sender_id=10)

    run = asyncio.create_task(handler.handle(command))
    await runner.started.wait()
    await asyncio.sleep(0.02)

    assert command.deleted is True
    assert run.done() is False

    runner.release.set()
    assert await run is True
