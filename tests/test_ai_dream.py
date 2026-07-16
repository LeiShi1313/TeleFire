import asyncio
import time
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from telethon.errors import FloodWaitError

from telefire.ai import (
    AIAnswerMarker,
    AIStateRepository,
    MemoryBackfillRequest,
    MessageIdentity,
    PromptBuilder,
)
from telefire.ai_dream import (
    ContinuousMemoryScheduler,
    ContinuousMemorySchedulerSettings,
    ContinuousMemoryScheduleResult,
    DreamCycleBusyError,
    DreamBackfillLimitError,
    DreamScheduleResult,
    DreamScheduler,
    DreamSchedulerSettings,
    DreamSettings,
    DreamThreadLimitError,
    TelegramDreamScanner,
    TelegramHistorySource,
)
from telefire.ai_attachments import AttachmentDescription
from telefire.ai_memory import MemoryClientError, MemoryRetainResult


NOW = datetime(2026, 7, 13, 12, 0, tzinfo=UTC)


class FakeMessage:
    def __init__(
        self,
        message_id,
        text,
        *,
        sender_id=20,
        reply_to=None,
        date=None,
        file=None,
        is_bot=False,
        post=False,
        grouped_id=None,
        post_author=None,
    ):
        self.id = message_id
        self.raw_text = text
        self.sender_id = sender_id
        self.chat_id = -1001
        self.reply_to_msg_id = reply_to.id if reply_to else None
        self.date = date or NOW - timedelta(minutes=5)
        self.file = file
        self.post = post
        self.grouped_id = grouped_id
        self.post_author = post_author
        self.entities = ()
        self.sender = type("Sender", (), {"bot": is_bot})()

    async def get_reply_message(self):
        return None


class FakeSource:
    def __init__(self, window, ancestors=()):
        self.window = tuple(window)
        self.by_id = {message.id: message for message in (*window, *ancestors)}
        self.window_calls = []
        self.message_calls = []

    async def fetch_window(
        self,
        chat_id,
        *,
        since,
        until,
        limit,
    ):
        self.window_calls.append(
            {
                "chat_id": chat_id,
                "since": since,
                "until": until,
                "limit": limit,
            }
        )
        eligible = tuple(
            message
            for message in self.window
            if since <= message.date <= until
        )
        return eligible[-limit:]

    async def fetch_message(self, chat_id, message_id):
        self.message_calls.append((chat_id, message_id))
        return self.by_id.get(message_id)

    async def fetch_after(
        self,
        chat_id,
        *,
        after_message_id,
        until,
        limit,
    ):
        self.window_calls.append(
            {
                "chat_id": chat_id,
                "after_message_id": after_message_id,
                "until": until,
                "limit": limit,
            }
        )
        return tuple(
            message
            for message in self.window
            if message.id > after_message_id and message.date <= until
        )[:limit]


@pytest.mark.asyncio
async def test_telegram_history_source_fetches_recent_messages_from_current_topic():
    older = FakeMessage(41, "Older topic message")
    newer = FakeMessage(42, "Newer topic message")

    class Client:
        def __init__(self):
            self.calls = []

        async def iter_messages(self, chat_id, **kwargs):
            self.calls.append((chat_id, kwargs))
            for message in (newer, older):
                yield message

    client = Client()
    trigger = FakeMessage(50, "/ai2 summarize")
    trigger.reply_to = type(
        "ReplyHeader",
        (),
        {
            "forum_topic": True,
            "reply_to_top_id": 12,
            "reply_to_msg_id": 40,
        },
    )()

    messages = await TelegramHistorySource(client).fetch_recent(trigger, limit=2)

    assert messages == (older, newer)
    assert client.calls == [
        (-1001, {"limit": 2, "max_id": 50, "reply_to": 12})
    ]


@pytest.mark.asyncio
async def test_telegram_history_source_uses_whole_chat_outside_forum_topics():
    message = FakeMessage(49, "Recent group message")

    class Client:
        def __init__(self):
            self.calls = []

        async def iter_messages(self, chat_id, **kwargs):
            self.calls.append((chat_id, kwargs))
            yield message

    client = Client()
    trigger = FakeMessage(50, "/ai1 summarize", reply_to=FakeMessage(48, "branch"))
    trigger.reply_to = type("ReplyHeader", (), {"forum_topic": False})()

    messages = await TelegramHistorySource(client).fetch_recent(trigger, limit=1)

    assert messages == (message,)
    assert client.calls == [(-1001, {"limit": 1, "max_id": 50})]


@pytest.mark.asyncio
async def test_telegram_history_source_fetches_after_cursor_oldest_first():
    older = FakeMessage(42, "First unseen message")
    newer = FakeMessage(43, "Second unseen message")

    class Client:
        def __init__(self):
            self.calls = []

        async def iter_messages(self, chat_id, **kwargs):
            self.calls.append((chat_id, kwargs))
            for message in (older, newer):
                yield message

    client = Client()
    messages = await TelegramHistorySource(client).fetch_after(
        -1001,
        after_message_id=41,
        until=NOW,
        limit=2,
    )

    assert messages == (older, newer)
    assert client.calls == [
        (
            -1001,
            {
                "min_id": 41,
                "reverse": True,
                "limit": 2,
            },
        )
    ]


@pytest.mark.asyncio
async def test_telegram_history_source_stops_before_unsettled_messages():
    settled = FakeMessage(42, "Settled", date=NOW - timedelta(seconds=31))
    unsettled = FakeMessage(43, "Unsettled", date=NOW)
    never_reached = FakeMessage(44, "Later", date=NOW + timedelta(seconds=1))

    class Client:
        async def iter_messages(self, chat_id, **kwargs):
            for message in (settled, unsettled, never_reached):
                yield message

    messages = await TelegramHistorySource(Client()).fetch_after(
        -1001,
        after_message_id=41,
        until=NOW - timedelta(seconds=30),
        limit=3,
    )

    assert messages == (settled,)


class FakeMemory:
    def __init__(self, *, fail_document=None):
        self.fail_document = fail_document
        self.retain_calls = []
        self.retain_batches = []

    async def retain(self, episode, *, update_mode="replace"):
        self.retain_calls.append({"episode": episode, "update_mode": update_mode})
        if episode.document_id == self.fail_document:
            raise ConnectionError("synthetic retain failure")
        return MemoryRetainResult(accepted=True)

    async def retain_many(self, episodes, *, update_mode="replace"):
        self.retain_batches.append(episodes)
        for episode in episodes:
            self.retain_calls.append({"episode": episode, "update_mode": update_mode})
        if any(episode.document_id == self.fail_document for episode in episodes):
            raise ConnectionError("synthetic retain failure")
        return MemoryRetainResult(accepted=True, items_count=len(episodes))


class FakeIdentityResolver:
    async def resolve(self, message):
        return MessageIdentity(
            subject_display_name=f"User {message.sender_id}",
            scope_display_name="Dream Group",
            is_human=not bool(getattr(message.sender, "bot", False)),
        )


class CountingIdentityResolver(FakeIdentityResolver):
    def __init__(self):
        self.calls = 0

    async def resolve(self, message):
        self.calls += 1
        return await super().resolve(message)


class BroadcastChannelIdentityResolver:
    async def resolve(self, message):
        return MessageIdentity(
            subject_id="telegram:channel:2064685671",
            subject_display_name="Seele Leaks",
            scope_display_name="Seele Leaks",
            is_human=False,
        )


class FakeAttachmentDescriber:
    def __init__(self):
        self.calls = []

    async def describe(self, message):
        self.calls.append(message.id)
        if message.file is None:
            return None
        return AttachmentDescription(
            context_text="A whiteboard sketch of the launch plan.",
            memory_text="Attachment description: launch-plan whiteboard sketch.",
        )


async def make_scanner(
    tmp_path,
    source,
    memory,
    *,
    max_thread_messages=20,
    attachment_describer=None,
    retain_concurrency=1,
    max_messages=100,
    lease_seconds=3_600,
    clock=lambda: NOW.timestamp(),
    identity_resolver=None,
):
    store = await AIStateRepository(tmp_path / "ai.db").connect()
    await store.set_dream_memory_enabled("telegram:chat:-1001", True, "Dream Group")
    scanner = TelegramDreamScanner(
        source=source,
        store=store,
        memory=memory,
        prompt_builder=PromptBuilder(
            identity_resolver=identity_resolver or FakeIdentityResolver(),
            attachment_describer=attachment_describer,
        ),
        settings=DreamSettings(
            lookback=timedelta(hours=1),
            overlap=timedelta(minutes=10),
            settlement_delay=timedelta(0),
            max_messages=max_messages,
            max_thread_messages=max_thread_messages,
            session_max_events=1,
            retain_concurrency=retain_concurrency,
            lease_seconds=lease_seconds,
        ),
        clock=clock,
    )
    return store, scanner


@pytest.mark.asyncio
async def test_manual_dream_retains_standalone_and_complete_reply_tree(tmp_path):
    standalone = FakeMessage(1, "Standalone project decision")
    root = FakeMessage(2, "Root plan", sender_id=30)
    first_reply = FakeMessage(3, "First branch", reply_to=root)
    second_reply = FakeMessage(4, "Second branch", sender_id=40, reply_to=root)
    control = FakeMessage(5, "/ai_memory_status", sender_id=10)
    ai_answer = FakeMessage(6, "Generated answer", sender_id=10)
    source = FakeSource(
        [standalone, root, first_reply, second_reply, control, ai_answer]
    )
    memory = FakeMemory()
    store, scanner = await make_scanner(tmp_path, source, memory)
    await store.save_answer(
        AIAnswerMarker(
            chat_id=-1001,
            answer_message_id=ai_answer.id,
            trigger_message_id=999,
            requester_id=20,
            prompt="old",
            answer_text=ai_answer.raw_text,
            parent_answer_message_id=None,
            reference_context="",
            agent_session_id="session-old",
            agent_entry_id="entry-old",
        )
    )
    await store.mark_memory_excluded_message(-1001, control.id, "memory-control")
    try:
        result = await scanner.run_scope(-1001)

        assert result.messages_seen == 6
        assert result.messages_retained == 4
        assert result.documents_created == 2
        assert result.documents_unchanged == 0
        episodes = {
            call["episode"].document_id: call["episode"] for call in memory.retain_calls
        }
        assert [
            event.text
            for event in episodes[
                "telegram:dream-session:-1001:20260713T115500Z:1"
            ].events
        ] == ["Standalone project decision"]
        assert [
            event.text
            for event in episodes[
                "telegram:dream-session:-1001:20260713T115500Z:2"
            ].events
        ] == ["Root plan", "First branch", "Second branch"]
        assert all(call["update_mode"] == "replace" for call in memory.retain_calls)
        assert [len(batch) for batch in memory.retain_batches] == [1, 1]
        state = await store.get_memory_dream_state("telegram:chat:-1001")
        assert state.cursor_message_id == 6
        assert state.scanned_until_at == NOW.timestamp()
        assert state.last_success_at == NOW.timestamp()
        assert state.last_error is None
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_dream_segments_root_groups_by_message_time(tmp_path):
    first = FakeMessage(
        1_000,
        "Morning plan",
        date=NOW - timedelta(minutes=50),
    )
    nearby = FakeMessage(
        9_000,
        "Nearby follow-up",
        date=NOW - timedelta(minutes=45),
    )
    after_idle_gap = FakeMessage(
        9_001,
        "Separate discussion",
        date=NOW - timedelta(minutes=20),
    )
    source = FakeSource((first, nearby, after_idle_gap))
    memory = FakeMemory()
    store = await AIStateRepository(tmp_path / "ai.db").connect()
    await store.set_dream_memory_enabled("telegram:chat:-1001", True, "Dream Group")
    scanner = TelegramDreamScanner(
        source=source,
        store=store,
        memory=memory,
        prompt_builder=PromptBuilder(identity_resolver=FakeIdentityResolver()),
        settings=DreamSettings(
            lookback=timedelta(hours=1),
            settlement_delay=timedelta(0),
            session_idle_gap=timedelta(minutes=15),
            session_max_span=timedelta(hours=1),
            session_max_events=30,
            session_max_chars=4_000,
        ),
        clock=lambda: NOW.timestamp(),
    )
    try:
        result = await scanner.run_scope(-1001)

        assert result.documents_created == 2
        episodes = [call["episode"] for call in memory.retain_calls]
        assert [episode.document_id for episode in episodes] == [
            "telegram:dream-session:-1001:20260713T111000Z:1000",
            "telegram:dream-session:-1001:20260713T114000Z:9001",
        ]
        assert [[event.text for event in episode.events] for episode in episodes] == [
            ["Morning plan", "Nearby follow-up"],
            ["Separate discussion"],
        ]
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_dream_keeps_an_oversized_reply_tree_atomic(tmp_path):
    root = FakeMessage(
        2_000,
        "Root",
        date=NOW - timedelta(minutes=20),
    )
    first_reply = FakeMessage(
        2_001,
        "First reply",
        reply_to=root,
        date=NOW - timedelta(minutes=19),
    )
    second_reply = FakeMessage(
        2_002,
        "Second reply",
        reply_to=first_reply,
        date=NOW - timedelta(minutes=18),
    )
    standalone = FakeMessage(
        2_003,
        "Nearby standalone",
        date=NOW - timedelta(minutes=17),
    )
    source = FakeSource((root, first_reply, second_reply, standalone))
    memory = FakeMemory()
    store = await AIStateRepository(tmp_path / "ai.db").connect()
    await store.set_dream_memory_enabled("telegram:chat:-1001", True, "Dream Group")
    scanner = TelegramDreamScanner(
        source=source,
        store=store,
        memory=memory,
        prompt_builder=PromptBuilder(identity_resolver=FakeIdentityResolver()),
        settings=DreamSettings(
            lookback=timedelta(hours=1),
            settlement_delay=timedelta(0),
            session_idle_gap=timedelta(minutes=15),
            session_max_span=timedelta(hours=1),
            session_max_events=2,
            session_max_chars=4_000,
        ),
        clock=lambda: NOW.timestamp(),
    )
    try:
        result = await scanner.run_scope(-1001)

        assert result.documents_created == 2
        assert [
            [event.text for event in call["episode"].events]
            for call in memory.retain_calls
        ] == [
            ["Root", "First reply", "Second reply"],
            ["Nearby standalone"],
        ]
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_dream_closes_a_continuous_session_at_size_and_span_bounds(tmp_path):
    messages = (
        FakeMessage(
            2_100,
            "1234567890",
            date=NOW - timedelta(minutes=30),
        ),
        FakeMessage(
            2_101,
            "a",
            date=NOW - timedelta(minutes=25),
        ),
        FakeMessage(
            2_102,
            "b",
            date=NOW - timedelta(minutes=20),
        ),
        FakeMessage(
            2_103,
            "c",
            date=NOW - timedelta(minutes=9),
        ),
    )
    source = FakeSource(messages)
    memory = FakeMemory()
    store = await AIStateRepository(tmp_path / "ai.db").connect()
    await store.set_dream_memory_enabled("telegram:chat:-1001", True, "Dream Group")
    scanner = TelegramDreamScanner(
        source=source,
        store=store,
        memory=memory,
        prompt_builder=PromptBuilder(identity_resolver=FakeIdentityResolver()),
        settings=DreamSettings(
            lookback=timedelta(hours=1),
            settlement_delay=timedelta(0),
            session_idle_gap=timedelta(minutes=15),
            session_max_span=timedelta(minutes=15),
            session_max_events=30,
            session_max_chars=10,
        ),
        clock=lambda: NOW.timestamp(),
    )
    try:
        result = await scanner.run_scope(-1001)

        assert result.documents_created == 3
        assert [
            [event.text for event in call["episode"].events]
            for call in memory.retain_calls
        ] == [["1234567890"], ["a", "b"], ["c"]]
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_dream_appends_to_an_open_temporal_session_after_restart(tmp_path):
    class MutableClock:
        value = (NOW - timedelta(minutes=5)).timestamp()

        def __call__(self):
            return self.value

    clock = MutableClock()
    first = FakeMessage(
        3_000,
        "Initial discussion",
        date=NOW - timedelta(minutes=10),
    )
    source = FakeSource((first,))
    memory = FakeMemory()
    settings = DreamSettings(
        lookback=timedelta(hours=1),
        settlement_delay=timedelta(0),
        session_idle_gap=timedelta(minutes=15),
        session_max_span=timedelta(hours=1),
        session_max_events=30,
        session_max_chars=4_000,
    )
    state_path = tmp_path / "ai.db"
    first_store = await AIStateRepository(state_path).connect()
    await first_store.set_dream_memory_enabled(
        "telegram:chat:-1001",
        True,
        "Dream Group",
    )
    first_scanner = TelegramDreamScanner(
        source=source,
        store=first_store,
        memory=memory,
        prompt_builder=PromptBuilder(identity_resolver=FakeIdentityResolver()),
        settings=settings,
        clock=clock,
    )
    await first_scanner.run_scope(-1001)
    await first_store.close()

    second = FakeMessage(
        3_001,
        "Nearby continuation",
        date=NOW - timedelta(minutes=2),
    )
    source.window = (second,)
    source.by_id[second.id] = second
    clock.value = NOW.timestamp()
    second_store = await AIStateRepository(state_path).connect()
    second_scanner = TelegramDreamScanner(
        source=source,
        store=second_store,
        memory=memory,
        prompt_builder=PromptBuilder(identity_resolver=FakeIdentityResolver()),
        settings=settings,
        clock=clock,
    )
    try:
        result = await second_scanner.run_scope(-1001)

        assert result.documents_created == 1
        assert len(memory.retain_calls) == 2
        assert memory.retain_calls[1]["episode"].document_id == (
            memory.retain_calls[0]["episode"].document_id
        )
        assert [event.text for event in memory.retain_calls[1]["episode"].events] == [
            "Initial discussion",
            "Nearby continuation",
        ]
        assert (-1001, first.id) in source.message_calls
    finally:
        await second_store.close()


@pytest.mark.asyncio
async def test_dream_bounds_a_continuous_session_and_retains_concurrently(
    tmp_path,
):
    class ConcurrentMemory(FakeMemory):
        def __init__(self):
            super().__init__()
            self.active = 0
            self.max_active = 0

        async def retain_many(self, episodes, *, update_mode="replace"):
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            try:
                await asyncio.sleep(0.01)
                return await super().retain_many(
                    episodes,
                    update_mode=update_mode,
                )
            finally:
                self.active -= 1

    messages = tuple(
        FakeMessage(
            1_000 + index,
            f"Message {index} records preference {index}",
            date=NOW - timedelta(minutes=20) + timedelta(seconds=index),
        )
        for index in range(100)
    )
    source = FakeSource(messages)
    memory = ConcurrentMemory()
    identity_resolver = CountingIdentityResolver()
    store = await AIStateRepository(tmp_path / "ai.db").connect()
    await store.set_dream_memory_enabled("telegram:chat:-1001", True, "Dream Group")
    scanner = TelegramDreamScanner(
        source=source,
        store=store,
        memory=memory,
        prompt_builder=PromptBuilder(identity_resolver=identity_resolver),
        settings=DreamSettings(
            lookback=timedelta(hours=1),
            overlap=timedelta(minutes=10),
            settlement_delay=timedelta(0),
            max_messages=100,
            session_max_events=20,
            retain_concurrency=4,
        ),
        clock=lambda: NOW.timestamp(),
    )
    try:
        result = await scanner.run_scope(-1001)

        assert result.messages_seen == 100
        assert result.messages_retained == 100
        assert result.documents_created == 5
        assert result.documents_unchanged == 0
        assert memory.max_active == 4
        assert identity_resolver.calls == 1
        episodes = [call["episode"] for call in memory.retain_calls]
        assert [episode.document_id for episode in episodes] == [
            "telegram:dream-session:-1001:20260713T114000Z:1000",
            "telegram:dream-session:-1001:20260713T114020Z:1020",
            "telegram:dream-session:-1001:20260713T114040Z:1040",
            "telegram:dream-session:-1001:20260713T114100Z:1060",
            "telegram:dream-session:-1001:20260713T114120Z:1080",
        ]
        assert [len(episode.events) for episode in episodes] == [20] * 5
        assert all(len(batch) == 1 for batch in memory.retain_batches)
        assert (
            await store.find_memory_document_id_for_source(
                "telegram:chat:-1001",
                "telegram:message:-1001:1007",
            )
            == "telegram:dream-session:-1001:20260713T114000Z:1000"
        )
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_temporal_session_keeps_sibling_context_for_a_late_reply(tmp_path):
    root = FakeMessage(100, "Root decision", date=NOW - timedelta(minutes=30))
    sibling_root = FakeMessage(
        105,
        "Nearby standalone context",
        sender_id=30,
        date=NOW - timedelta(minutes=20),
    )
    first_reply = FakeMessage(
        106,
        "Earlier reply",
        sender_id=40,
        reply_to=root,
        date=NOW - timedelta(minutes=10),
    )
    source = FakeSource([root, sibling_root, first_reply])
    memory = FakeMemory()
    store = await AIStateRepository(tmp_path / "ai.db").connect()
    await store.set_dream_memory_enabled("telegram:chat:-1001", True, "Dream Group")
    scanner = TelegramDreamScanner(
        source=source,
        store=store,
        memory=memory,
        prompt_builder=PromptBuilder(identity_resolver=FakeIdentityResolver()),
        settings=DreamSettings(
            lookback=timedelta(hours=1),
            overlap=timedelta(minutes=10),
            settlement_delay=timedelta(0),
            retain_concurrency=1,
        ),
        clock=lambda: NOW.timestamp(),
    )
    try:
        await scanner.run_scope(-1001)

        late_reply = FakeMessage(
            150,
            "Late reply",
            sender_id=50,
            reply_to=root,
            date=NOW - timedelta(minutes=5),
        )
        source.window = (late_reply,)
        source.by_id[late_reply.id] = late_reply
        await scanner.run_scope(-1001)

        updated = memory.retain_calls[-1]["episode"]
        assert updated.document_id == (
            "telegram:dream-session:-1001:20260713T113000Z:100"
        )
        assert [event.source_id for event in updated.events] == [
            "telegram:message:-1001:100",
            "telegram:message:-1001:105",
            "telegram:message:-1001:106",
            "telegram:message:-1001:150",
        ]
        assert (-1001, sibling_root.id) in source.message_calls
        assert (-1001, first_reply.id) in source.message_calls
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_late_reply_preserves_a_legacy_packed_document(tmp_path):
    root = FakeMessage(160, "Legacy root", date=NOW - timedelta(minutes=30))
    sibling = FakeMessage(161, "Legacy sibling", date=NOW - timedelta(minutes=25))
    first_reply = FakeMessage(
        162,
        "Legacy reply",
        reply_to=root,
        date=NOW - timedelta(minutes=20),
    )
    late_reply = FakeMessage(
        190,
        "New late reply",
        reply_to=root,
        date=NOW - timedelta(minutes=5),
    )
    source = FakeSource((late_reply,), ancestors=(root, sibling, first_reply))
    memory = FakeMemory()
    store = await AIStateRepository(tmp_path / "ai.db").connect()
    await store.set_dream_memory_enabled("telegram:chat:-1001", True, "Dream Group")
    document_id = "telegram:dream-segment:-1001:160-179"
    await store.save_memory_document_receipt(
        "telegram:chat:-1001",
        document_id,
        "legacy-content",
        tuple(
            (f"telegram:message:-1001:{message.id}", "legacy-event")
            for message in (root, sibling, first_reply)
        ),
    )
    scanner = TelegramDreamScanner(
        source=source,
        store=store,
        memory=memory,
        prompt_builder=PromptBuilder(identity_resolver=FakeIdentityResolver()),
        settings=DreamSettings(
            lookback=timedelta(hours=1),
            settlement_delay=timedelta(0),
        ),
        clock=lambda: NOW.timestamp(),
    )
    try:
        result = await scanner.run_scope(-1001)

        assert result.documents_created == 1
        updated = memory.retain_calls[0]["episode"]
        assert updated.document_id == document_id
        assert [event.text for event in updated.events] == [
            "Legacy root",
            "Legacy sibling",
            "Legacy reply",
            "New late reply",
        ]
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_temporal_sessions_preserve_existing_thread_document_receipts(tmp_path):
    messages = (
        FakeMessage(200, "Existing first document"),
        FakeMessage(201, "Existing second document"),
    )
    source = FakeSource(messages)
    memory = FakeMemory()
    store = await AIStateRepository(tmp_path / "ai.db").connect()
    await store.set_dream_memory_enabled("telegram:chat:-1001", True, "Dream Group")
    for message in messages:
        await store.save_memory_document_receipt(
            "telegram:chat:-1001",
            f"telegram:thread:-1001:{message.id}",
            "legacy-content",
            ((f"telegram:message:-1001:{message.id}", "legacy-event"),),
        )
    try:
        scanner = TelegramDreamScanner(
            source=source,
            store=store,
            memory=memory,
            prompt_builder=PromptBuilder(identity_resolver=FakeIdentityResolver()),
            settings=DreamSettings(
                lookback=timedelta(hours=1),
                overlap=timedelta(minutes=10),
                settlement_delay=timedelta(0),
                retain_concurrency=4,
            ),
            clock=lambda: NOW.timestamp(),
        )
        result = await scanner.run_scope(-1001)

        assert result.documents_created == 2
        assert result.documents_unchanged == 0
        assert [call["episode"].document_id for call in memory.retain_calls] == [
            "telegram:thread:-1001:200",
            "telegram:thread:-1001:201",
        ]
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_budgeted_dream_advances_window_after_dropping_unprocessed_tail(tmp_path):
    class MutableMonotonic:
        value = 0.0

        def __call__(self):
            return self.value

    monotonic = MutableMonotonic()

    class TimedMemory(FakeMemory):
        async def retain_many(self, episodes, *, update_mode="replace"):
            result = await super().retain_many(episodes, update_mode=update_mode)
            monotonic.value += 30
            return result

    messages = tuple(
        FakeMessage(
            2_000 + index,
            f"Checkpoint message {index}",
            date=NOW - timedelta(minutes=3 - index),
        )
        for index in range(3)
    )
    source = FakeSource(messages)
    memory = TimedMemory()
    store = await AIStateRepository(tmp_path / "ai.db").connect()
    await store.set_dream_memory_enabled("telegram:chat:-1001", True, "Dream Group")
    scanner = TelegramDreamScanner(
        source=source,
        store=store,
        memory=memory,
        prompt_builder=PromptBuilder(identity_resolver=FakeIdentityResolver()),
        settings=DreamSettings(
            lookback=timedelta(hours=1),
            overlap=timedelta(minutes=10),
            settlement_delay=timedelta(0),
            max_messages=100,
            session_max_events=1,
            retain_concurrency=1,
            cycle_budget_seconds=25,
        ),
        clock=lambda: NOW.timestamp(),
        monotonic=monotonic,
    )
    try:
        first = await scanner.run_scope(-1001)

        assert first.messages_retained == 1
        assert first.documents_created == 1
        state = await store.get_memory_dream_state("telegram:chat:-1001")
        assert state.cursor_message_id == 2_002
        assert state.scanned_until_at == NOW.timestamp()

        resumed = TelegramDreamScanner(
            source=source,
            store=store,
            memory=memory,
            prompt_builder=PromptBuilder(identity_resolver=FakeIdentityResolver()),
            settings=DreamSettings(
                lookback=timedelta(hours=1),
                overlap=timedelta(minutes=10),
                settlement_delay=timedelta(0),
                max_messages=100,
                session_max_events=1,
                retain_concurrency=1,
                cycle_budget_seconds=3_600,
            ),
            clock=lambda: NOW.timestamp(),
            monotonic=monotonic,
        )
        second = await resumed.run_scope(-1001)

        assert second.messages_retained == 3
        assert second.documents_created == 2
        assert second.documents_unchanged == 1
        completed = await store.get_memory_dream_state("telegram:chat:-1001")
        assert completed.cursor_message_id == 2_002
        assert completed.scanned_until_at == NOW.timestamp()
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_dream_fetches_ancestor_outside_window_as_context(tmp_path):
    ancestor = FakeMessage(
        10,
        "Earlier root context",
        sender_id=30,
        date=NOW - timedelta(hours=2),
    )
    reply = FakeMessage(11, "New reply", reply_to=ancestor)
    source = FakeSource([reply], ancestors=[ancestor])
    memory = FakeMemory()
    store, scanner = await make_scanner(tmp_path, source, memory)
    try:
        result = await scanner.run_scope(-1001)

        assert result.messages_seen == 1
        assert result.messages_retained == 1
        assert result.documents_created == 1
        episode = memory.retain_calls[0]["episode"]
        assert episode.document_id == (
            "telegram:dream-session:-1001:20260713T100000Z:10"
        )
        assert [event.text for event in episode.events] == [
            "Earlier root context",
            "New reply",
        ]
        assert source.message_calls == [(-1001, 10)]
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_dream_overlap_is_idempotent_through_document_receipts(tmp_path):
    message = FakeMessage(20, "Stable standalone evidence")
    source = FakeSource([message])
    memory = FakeMemory()
    store, scanner = await make_scanner(tmp_path, source, memory)
    try:
        first = await scanner.run_scope(-1001)
        second = await scanner.run_scope(-1001)

        assert first.documents_created == 1
        assert second.documents_created == 0
        assert second.documents_unchanged == 1
        assert len(memory.retain_calls) == 1
        assert source.window_calls[1]["since"] == NOW - timedelta(minutes=10)
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_days_backfill_uses_rolling_window_without_moving_dream_watermark(
    tmp_path,
):
    outside = FakeMessage(
        21,
        "Older than the requested window",
        date=NOW - timedelta(days=8),
    )
    inside = FakeMessage(
        22,
        "Evidence from this week",
        date=NOW - timedelta(days=2),
    )
    source = FakeSource([outside, inside])
    memory = FakeMemory()
    store, scanner = await make_scanner(tmp_path, source, memory)
    previous_watermark = (NOW - timedelta(hours=3)).timestamp()
    await store.record_memory_dream_success(
        "telegram:chat:-1001",
        cursor_message_id=900,
        scanned_until_at=previous_watermark,
        succeeded_at=previous_watermark,
    )
    before = await store.get_memory_dream_state("telegram:chat:-1001")
    try:
        result = await scanner.run_backfill(
            -1001,
            MemoryBackfillRequest(mode="days", value=7),
        )

        assert result.messages_seen == 1
        assert result.messages_retained == 1
        assert [event.text for event in memory.retain_calls[0]["episode"].events] == [
            "Evidence from this week"
        ]
        assert source.window_calls == [
            {
                "chat_id": -1001,
                "since": NOW - timedelta(days=7),
                "until": NOW,
                "limit": 5_001,
            }
        ]
        assert await store.get_memory_dream_state("telegram:chat:-1001") == before
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_message_backfill_works_while_disabled_and_is_idempotent(tmp_path):
    first = FakeMessage(31, "Old first", date=NOW - timedelta(days=10))
    second = FakeMessage(32, "Recent second", date=NOW - timedelta(days=2))
    third = FakeMessage(33, "Recent third", date=NOW - timedelta(days=1))
    source = FakeSource([first, second, third])
    memory = FakeMemory()
    store, scanner = await make_scanner(tmp_path, source, memory)
    await store.set_dream_memory_enabled("telegram:chat:-1001", False)
    request = MemoryBackfillRequest(mode="messages", value=2)
    try:
        first_result = await scanner.run_backfill(-1001, request)
        second_result = await scanner.run_backfill(-1001, request)

        assert first_result.messages_seen == 2
        assert first_result.messages_retained == 2
        assert first_result.documents_created == 2
        assert second_result.documents_created == 0
        assert second_result.documents_unchanged == 2
        assert [call["episode"].events[0].text for call in memory.retain_calls] == [
            "Recent second",
            "Recent third",
        ]
        assert all(call["since"].year == 1 for call in source.window_calls)
        assert all(call["limit"] == 2 for call in source.window_calls)
        state = await store.get_memory_dream_state("telegram:chat:-1001")
        assert state.scanned_until_at is None
        assert state.last_attempt_at is None
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_continuous_memory_resumes_after_cursor_and_checkpoints_success(tmp_path):
    first = FakeMessage(42, "First unseen message")
    second = FakeMessage(43, "Second unseen message")
    source = FakeSource([first, second])
    memory = FakeMemory()
    store, scanner = await make_scanner(
        tmp_path,
        source,
        memory,
        max_messages=2,
    )
    await store.set_dream_memory_enabled("telegram:chat:-1001", False)
    await store.set_continuous_memory_enabled(
        "telegram:chat:-1001",
        True,
        "Dream Group",
        cursor_message_id=41,
    )
    try:
        result = await scanner.run_continuous_scope(-1001)

        assert result.messages_seen == 2
        assert result.messages_retained == 2
        assert result.caught_up is False
        assert source.window_calls == [
            {
                "chat_id": -1001,
                "after_message_id": 41,
                "until": NOW,
                "limit": 2,
            }
        ]
        state = await store.get_memory_scope_state("telegram:chat:-1001")
        assert state.continuous_cursor_message_id == 43
        assert state.continuous_last_attempt_at == NOW.timestamp()
        assert state.continuous_last_success_at == NOW.timestamp()
        assert state.continuous_last_error is None
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_continuous_memory_skips_excluded_messages_without_stalling_cursor(
    tmp_path,
):
    excluded = FakeMessage(42, "/ai_memory_status")
    retained = FakeMessage(43, "Useful evidence")
    source = FakeSource([excluded, retained])
    memory = FakeMemory()
    store, scanner = await make_scanner(tmp_path, source, memory)
    await store.set_dream_memory_enabled("telegram:chat:-1001", False)
    await store.set_continuous_memory_enabled(
        "telegram:chat:-1001",
        True,
        cursor_message_id=41,
    )
    await store.mark_memory_excluded_message(-1001, 42, "memory-control")
    try:
        result = await scanner.run_continuous_scope(-1001)

        assert result.messages_seen == 2
        assert result.messages_retained == 1
        assert (
            await store.get_memory_scope_state("telegram:chat:-1001")
        ).continuous_cursor_message_id == 43
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_continuous_memory_checkpoints_accepted_prefix_before_failure(tmp_path):
    first = FakeMessage(
        42,
        "First independent document",
        date=NOW - timedelta(minutes=30),
    )
    second = FakeMessage(
        43,
        "Second independent document",
        date=NOW - timedelta(minutes=5),
    )
    source = FakeSource([first, second])
    memory = FakeMemory(
        fail_document="telegram:dream-session:-1001:20260713T115500Z:43"
    )
    store, scanner = await make_scanner(
        tmp_path,
        source,
        memory,
        retain_concurrency=1,
    )
    await store.set_continuous_memory_enabled(
        "telegram:chat:-1001",
        True,
        cursor_message_id=41,
    )
    try:
        with pytest.raises(ConnectionError, match="synthetic"):
            await scanner.run_continuous_scope(-1001)

        state = await store.get_memory_scope_state("telegram:chat:-1001")
        assert state.continuous_cursor_message_id == 42
        assert state.continuous_last_success_at == NOW.timestamp()
        assert "ConnectionError" in (state.continuous_last_error or "")
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_continuous_memory_ignores_dream_control_messages_without_markers(
    tmp_path,
):
    command = FakeMessage(42, "/ai_dream_enable")
    source = FakeSource([command])
    memory = FakeMemory()
    store, scanner = await make_scanner(tmp_path, source, memory)
    await store.set_continuous_memory_enabled(
        "telegram:chat:-1001",
        True,
        cursor_message_id=41,
    )
    try:
        result = await scanner.run_continuous_scope(-1001)

        assert result.messages_seen == 1
        assert result.messages_retained == 0
        assert memory.retain_calls == []
        assert (
            await store.get_memory_scope_state("telegram:chat:-1001")
        ).continuous_cursor_message_id == 42
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_days_backfill_rejects_a_window_above_its_separate_limit(tmp_path):
    repeated = FakeMessage(40, "High volume", date=NOW - timedelta(hours=1))
    source = FakeSource([repeated] * 5_001)
    memory = FakeMemory()
    store, scanner = await make_scanner(tmp_path, source, memory)
    try:
        with pytest.raises(DreamBackfillLimitError, match="5,000"):
            await scanner.run_backfill(
                -1001,
                MemoryBackfillRequest(mode="days", value=1),
            )

        assert memory.retain_calls == []
        state = await store.get_memory_dream_state("telegram:chat:-1001")
        assert state.scanned_until_at is None
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_dream_updates_existing_root_for_late_reply_and_edit(tmp_path):
    root = FakeMessage(20, "Original standalone evidence")
    source = FakeSource([root])
    memory = FakeMemory()
    store, scanner = await make_scanner(tmp_path, source, memory)
    try:
        first = await scanner.run_scope(-1001)
        assert first.documents_created == 1

        reply = FakeMessage(21, "Late reply", reply_to=root)
        source.window = (root, reply)
        source.by_id[reply.id] = reply
        second = await scanner.run_scope(-1001)

        assert second.documents_created == 1
        assert memory.retain_calls[-1]["update_mode"] == "replace"
        assert [event.text for event in memory.retain_calls[-1]["episode"].events] == [
            "Original standalone evidence",
            "Late reply",
        ]

        root.raw_text = "Edited root evidence"
        third = await scanner.run_scope(-1001)
        assert third.documents_created == 1
        assert [call["episode"].document_id for call in memory.retain_calls] == [
            "telegram:dream-session:-1001:20260713T115500Z:20",
            "telegram:dream-session:-1001:20260713T115500Z:20",
            "telegram:dream-session:-1001:20260713T115500Z:20",
        ]
        assert memory.retain_calls[-1]["update_mode"] == "replace"
        assert [event.text for event in memory.retain_calls[-1]["episode"].events] == [
            "Edited root evidence",
            "Late reply",
        ]
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_dream_preserves_a_sibling_reply_outside_the_overlap_window(tmp_path):
    root = FakeMessage(30, "Root evidence", date=NOW - timedelta(minutes=50))
    first_reply = FakeMessage(
        31,
        "Earlier sibling reply",
        reply_to=root,
        date=NOW - timedelta(minutes=40),
    )
    source = FakeSource([root, first_reply])
    memory = FakeMemory()
    store, scanner = await make_scanner(tmp_path, source, memory)
    try:
        await scanner.run_scope(-1001)

        later_reply = FakeMessage(
            32,
            "Later sibling reply",
            reply_to=root,
            date=NOW - timedelta(minutes=5),
        )
        source.window = (later_reply,)
        source.by_id[later_reply.id] = later_reply
        await scanner.run_scope(-1001)

        assert [event.text for event in memory.retain_calls[-1]["episode"].events] == [
            "Root evidence",
            "Earlier sibling reply",
            "Later sibling reply",
        ]
        assert (-1001, first_reply.id) in source.message_calls
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_dream_excludes_marked_messages_bots_and_keeps_attachment_text(tmp_path):
    excluded = FakeMessage(40, "Generated control acknowledgement", sender_id=10)
    bot = FakeMessage(41, "Automated bot post", sender_id=99, is_bot=True)
    attachment = FakeMessage(42, "", file=object())
    source = FakeSource([excluded, bot, attachment])
    memory = FakeMemory()
    store, scanner = await make_scanner(
        tmp_path,
        source,
        memory,
        attachment_describer=FakeAttachmentDescriber(),
    )
    await store.mark_memory_excluded_message(-1001, excluded.id, "memory-control")
    try:
        result = await scanner.run_scope(-1001)

        assert result.messages_seen == 3
        assert result.messages_retained == 1
        assert result.documents_created == 1
        assert memory.retain_calls[0]["episode"].events[0].text == (
            "Attachment description: launch-plan whiteboard sketch."
        )
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_dream_retains_broadcast_channel_posts_with_channel_actor(tmp_path):
    post = FakeMessage(
        43,
        "Version 3.4 preview notes",
        sender_id=-1002064685671,
    )
    source = FakeSource([post])
    memory = FakeMemory()
    store, scanner = await make_scanner(
        tmp_path,
        source,
        memory,
        identity_resolver=BroadcastChannelIdentityResolver(),
    )
    try:
        result = await scanner.run_scope(-1001)

        assert result.messages_retained == 1
        assert memory.retain_calls[0]["episode"].events[0].actor_id == (
            "telegram:channel:2064685671"
        )
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_channel_album_is_one_document_with_one_analyzed_attachment(tmp_path):
    album = (
        FakeMessage(
            44,
            "Preview album",
            sender_id=-1002064685671,
            file=SimpleNamespace(
                name="preview-1.jpg",
                mime_type="image/jpeg",
                size=100,
            ),
            post=True,
            grouped_id=777,
            post_author="Release Team",
        ),
        FakeMessage(
            45,
            "",
            sender_id=-1002064685671,
            file=SimpleNamespace(
                name="preview-2.jpg",
                mime_type="image/jpeg",
                size=100,
            ),
            post=True,
            grouped_id=777,
        ),
    )
    source = FakeSource(album)
    memory = FakeMemory()
    attachment_describer = FakeAttachmentDescriber()
    store, scanner = await make_scanner(
        tmp_path,
        source,
        memory,
        attachment_describer=attachment_describer,
        identity_resolver=BroadcastChannelIdentityResolver(),
    )
    try:
        result = await scanner.run_scope(-1001)

        assert result.messages_retained == 2
        assert result.documents_created == 1
        episode = memory.retain_calls[0]["episode"]
        assert episode.document_id == "telegram:channel-album:-1001:777"
        assert [event.source_id for event in episode.events] == [
            "telegram:message:-1001:44",
            "telegram:message:-1001:45",
        ]
        assert attachment_describer.calls == [44]
        assert episode.events[0].metadata["post_author"] == "Release Team"
        assert "content was not analyzed" in episode.events[1].text.lower()
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_channel_post_preserves_existing_document_identity(tmp_path):
    post = FakeMessage(
        46,
        "Existing retained channel post",
        sender_id=-1002064685671,
        post=True,
    )
    source = FakeSource([post])
    memory = FakeMemory()
    store, scanner = await make_scanner(
        tmp_path,
        source,
        memory,
        identity_resolver=BroadcastChannelIdentityResolver(),
    )
    legacy_document_id = "telegram:dream-session:-1001:20260713T115500Z:46"
    await store.save_memory_document_receipt(
        "telegram:chat:-1001",
        legacy_document_id,
        "legacy-content",
        (("telegram:message:-1001:46", "legacy-event"),),
    )
    try:
        await scanner.run_scope(-1001)

        assert memory.retain_calls[0]["episode"].document_id == legacy_document_id
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_thread_limit_refuses_to_advance_without_a_stable_complete_root(tmp_path):
    root = FakeMessage(50, "Root")
    reply = FakeMessage(51, "Reply", reply_to=root)
    source = FakeSource([root, reply])
    memory = FakeMemory()
    store, scanner = await make_scanner(
        tmp_path,
        source,
        memory,
        max_thread_messages=1,
    )
    try:
        with pytest.raises(DreamThreadLimitError, match="exceeds"):
            await scanner.run_scope(-1001)
        state = await store.get_memory_dream_state("telegram:chat:-1001")
        assert state.cursor_message_id is None
        assert state.scanned_until_at is None
        assert memory.retain_calls == []
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_window_over_limit_keeps_newest_messages_and_advances_watermark(tmp_path):
    messages = [
        FakeMessage(
            80 + index,
            f"Message {index}",
            date=NOW - timedelta(minutes=3 - index),
        )
        for index in range(3)
    ]
    source = FakeSource(messages)
    memory = FakeMemory()
    store, scanner = await make_scanner(
        tmp_path,
        source,
        memory,
        max_messages=2,
    )
    try:
        first = await scanner.run_scope(-1001)
        first_state = await store.get_memory_dream_state("telegram:chat:-1001")

        assert first.messages_seen == 2
        assert first.messages_retained == 2
        assert first_state.cursor_message_id == 82
        assert first_state.scanned_until_at == NOW.timestamp()
        assert first_state.last_error is None
        assert [
            call["episode"].events[0].source_id for call in memory.retain_calls
        ] == [
            "telegram:message:-1001:81",
            "telegram:message:-1001:82",
        ]

        second = await scanner.run_scope(-1001)
        completed = await store.get_memory_dream_state("telegram:chat:-1001")

        assert second.documents_created == 0
        assert second.documents_unchanged == 2
        assert completed.cursor_message_id == 82
        assert completed.scanned_until_at == NOW.timestamp()
        assert all(call["limit"] == 2 for call in source.window_calls)
        assert [
            call["episode"].events[0].source_id for call in memory.retain_calls
        ] == [
            "telegram:message:-1001:81",
            "telegram:message:-1001:82",
        ]
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_next_scan_starts_from_scanned_watermark_not_slow_completion(tmp_path):
    class MutableClock:
        value = NOW.timestamp()

        def __call__(self):
            return self.value

    clock = MutableClock()

    class SlowMemory(FakeMemory):
        async def retain_many(self, episodes, *, update_mode="replace"):
            result = await super().retain_many(episodes, update_mode=update_mode)
            clock.value += timedelta(minutes=30).total_seconds()
            return result

    first = FakeMessage(90, "Before the first watermark")
    source = FakeSource([first])
    memory = SlowMemory()
    store, scanner = await make_scanner(
        tmp_path,
        source,
        memory,
        clock=clock,
    )
    try:
        await scanner.run_scope(-1001)
        state = await store.get_memory_dream_state("telegram:chat:-1001")
        assert state.scanned_until_at == NOW.timestamp()
        assert state.last_success_at == NOW.timestamp() + 30 * 60

        arrived_during_run = FakeMessage(
            91,
            "Arrived while retention was slow",
            date=NOW + timedelta(minutes=5),
        )
        source.window = (first, arrived_during_run)
        source.by_id[arrived_during_run.id] = arrived_during_run
        await scanner.run_scope(-1001)

        assert source.window_calls[1]["since"] == NOW - timedelta(minutes=10)
        assert any(
            event.text == "Arrived while retention was slow"
            for call in memory.retain_calls
            for event in call["episode"].events
        )
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_document_receipts_survive_scanner_restart(tmp_path):
    state_path = tmp_path / "ai.db"
    message = FakeMessage(60, "Restart-safe evidence")
    source = FakeSource([message])
    memory = FakeMemory()

    first_store, first_scanner = await make_scanner(tmp_path, source, memory)
    await first_scanner.run_scope(-1001)
    await first_store.close()

    second_store = await AIStateRepository(state_path).connect()
    second_scanner = TelegramDreamScanner(
        source=source,
        store=second_store,
        memory=memory,
        prompt_builder=PromptBuilder(identity_resolver=FakeIdentityResolver()),
        settings=DreamSettings(settlement_delay=timedelta(0)),
        clock=lambda: NOW.timestamp(),
    )
    try:
        result = await second_scanner.run_scope(-1001)
        assert result.documents_created == 0
        assert result.documents_unchanged == 1
        assert len(memory.retain_calls) == 1
    finally:
        await second_store.close()


@pytest.mark.asyncio
async def test_dream_uses_settlement_delay_and_bounds_flood_wait_retry(tmp_path):
    class FloodSource(FakeSource):
        def __init__(self, window):
            super().__init__(window)
            self.attempts = 0

        async def fetch_window(self, chat_id, **kwargs):
            self.attempts += 1
            if self.attempts == 1:
                raise FloodWaitError(request=None, capture=20)
            return await super().fetch_window(chat_id, **kwargs)

    source = FloodSource([FakeMessage(70, "Delayed evidence")])
    memory = FakeMemory()
    delays = []
    store = await AIStateRepository(tmp_path / "ai.db").connect()
    await store.set_dream_memory_enabled("telegram:chat:-1001", True)
    scanner = TelegramDreamScanner(
        source=source,
        store=store,
        memory=memory,
        prompt_builder=PromptBuilder(identity_resolver=FakeIdentityResolver()),
        settings=DreamSettings(
            lookback=timedelta(hours=1),
            settlement_delay=timedelta(minutes=2),
            retry_attempts=2,
            max_retry_delay=3,
        ),
        clock=lambda: NOW.timestamp(),
        sleep=lambda delay: _record_delay(delays, delay),
    )
    try:
        await scanner.run_scope(-1001)
        assert delays == [3]
        assert source.window_calls[0]["until"] == NOW - timedelta(minutes=2)
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_dream_retries_hindsight_backpressure_with_bounded_delay(tmp_path):
    class BackpressureMemory(FakeMemory):
        def __init__(self):
            super().__init__()
            self.attempts = 0

        async def retain_many(self, episodes, *, update_mode="replace"):
            self.attempts += 1
            if self.attempts == 1:
                raise MemoryClientError(
                    "busy",
                    status=503,
                    retry_after=20,
                )
            return await super().retain_many(episodes, update_mode=update_mode)

    source = FakeSource([FakeMessage(71, "Backpressured evidence")])
    memory = BackpressureMemory()
    delays = []
    store = await AIStateRepository(tmp_path / "ai.db").connect()
    await store.set_dream_memory_enabled("telegram:chat:-1001", True)
    scanner = TelegramDreamScanner(
        source=source,
        store=store,
        memory=memory,
        prompt_builder=PromptBuilder(identity_resolver=FakeIdentityResolver()),
        settings=DreamSettings(
            settlement_delay=timedelta(0),
            retry_attempts=2,
            max_retry_delay=2,
        ),
        clock=lambda: NOW.timestamp(),
        sleep=lambda delay: _record_delay(delays, delay),
    )
    try:
        result = await scanner.run_scope(-1001)
        assert result.documents_created == 1
        assert memory.attempts == 2
        assert delays == [2]
    finally:
        await store.close()


async def _record_delay(delays, delay):
    delays.append(delay)


@pytest.mark.asyncio
async def test_dream_lease_is_durable_and_expires_after_restart(tmp_path):
    state_path = tmp_path / "ai.db"
    first = await AIStateRepository(state_path).connect()
    assert await first.acquire_memory_dream_lease(
        "telegram:chat:-1001",
        owner="worker-one",
        acquired_at=100,
        lease_seconds=60,
    )
    await first.close()

    second = await AIStateRepository(state_path).connect()
    try:
        assert not await second.acquire_memory_dream_lease(
            "telegram:chat:-1001",
            owner="worker-two",
            acquired_at=120,
            lease_seconds=60,
        )
        assert await second.acquire_memory_dream_lease(
            "telegram:chat:-1001",
            owner="worker-two",
            acquired_at=161,
            lease_seconds=60,
        )
    finally:
        await second.close()


@pytest.mark.asyncio
async def test_running_dream_renews_lease_until_work_finishes(tmp_path):
    started = asyncio.Event()
    release = asyncio.Event()

    class BlockingSource(FakeSource):
        async def fetch_window(self, chat_id, **kwargs):
            started.set()
            await release.wait()
            return await super().fetch_window(chat_id, **kwargs)

    source = BlockingSource([])
    memory = FakeMemory()
    store, scanner = await make_scanner(
        tmp_path,
        source,
        memory,
        lease_seconds=0.15,
        clock=time.time,
    )
    task = asyncio.create_task(scanner.run_scope(-1001))
    try:
        await asyncio.wait_for(started.wait(), timeout=1)
        await asyncio.sleep(0.22)
        assert not await store.acquire_memory_dream_lease(
            "telegram:chat:-1001",
            owner="competing-worker",
            acquired_at=time.time(),
            lease_seconds=1,
        )
        release.set()
        assert (await asyncio.wait_for(task, timeout=1)).messages_seen == 0
    finally:
        release.set()
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await store.close()


@pytest.mark.asyncio
async def test_timed_out_scope_does_not_block_other_scopes_or_later_runs(tmp_path):
    class PartiallyBlockingSource(FakeSource):
        def __init__(self):
            super().__init__([])
            self.blocked_calls = 0
            self.healthy_calls = 0
            self.cancelled_calls = 0

        async def fetch_window(self, chat_id, **kwargs):
            if chat_id != -1001:
                self.healthy_calls += 1
                return ()
            self.blocked_calls += 1
            try:
                await asyncio.Event().wait()
            finally:
                self.cancelled_calls += 1

    store = await AIStateRepository(tmp_path / "ai.db").connect()
    for chat_id in (-1001, -1002):
        await store.set_dream_memory_enabled(f"telegram:chat:{chat_id}", True)
    source = PartiallyBlockingSource()
    scanner = TelegramDreamScanner(
        source=source,
        store=store,
        memory=FakeMemory(),
        prompt_builder=PromptBuilder(identity_resolver=FakeIdentityResolver()),
        settings=DreamSettings(
            settlement_delay=timedelta(0),
            scope_timeout_seconds=0.03,
        ),
        clock=lambda: NOW.timestamp(),
    )
    scheduler = DreamScheduler(
        scanner=scanner,
        store=store,
        settings=DreamSchedulerSettings(cron=None, concurrency=2),
    )
    try:
        first = await asyncio.wait_for(scheduler.run_once(), timeout=1)
        second = await asyncio.wait_for(scheduler.run_once(), timeout=1)

        assert first == DreamScheduleResult(
            scopes_seen=2,
            scopes_succeeded=1,
            scopes_failed=1,
            scopes_busy=0,
        )
        assert second == first
        assert source.blocked_calls == 2
        assert source.healthy_calls == 2
        assert source.cancelled_calls == 2
        state = await store.get_memory_dream_state("telegram:chat:-1001")
        assert state.lease_owner is None
        assert state.lease_expires_at is None
        assert "DreamCycleTimeoutError" in (state.last_error or "")
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_scope_timeout_includes_waiting_for_an_existing_operation(tmp_path):
    backfill_started = asyncio.Event()
    release_backfill = asyncio.Event()

    class BlockingBackfillSource(FakeSource):
        async def fetch_window(self, chat_id, **kwargs):
            if chat_id == -1001:
                backfill_started.set()
                await release_backfill.wait()
            return ()

    store = await AIStateRepository(tmp_path / "ai.db").connect()
    for chat_id in (-1001, -1002):
        await store.set_dream_memory_enabled(f"telegram:chat:{chat_id}", True)
    scanner = TelegramDreamScanner(
        source=BlockingBackfillSource([]),
        store=store,
        memory=FakeMemory(),
        prompt_builder=PromptBuilder(identity_resolver=FakeIdentityResolver()),
        settings=DreamSettings(
            settlement_delay=timedelta(0),
            scope_timeout_seconds=0.03,
        ),
        clock=lambda: NOW.timestamp(),
    )
    backfill = asyncio.create_task(
        scanner.run_backfill(
            -1001,
            MemoryBackfillRequest(mode="messages", value=1),
        )
    )
    try:
        await asyncio.wait_for(backfill_started.wait(), timeout=1)

        result = await asyncio.wait_for(
            DreamScheduler(
                scanner=scanner,
                store=store,
                settings=DreamSchedulerSettings(cron=None, concurrency=2),
            ).run_once(),
            timeout=1,
        )

        assert result == DreamScheduleResult(
            scopes_seen=2,
            scopes_succeeded=1,
            scopes_failed=1,
            scopes_busy=0,
        )
        state = await store.get_memory_dream_state("telegram:chat:-1001")
        assert "DreamCycleTimeoutError" in (state.last_error or "")
    finally:
        release_backfill.set()
        await asyncio.gather(backfill, return_exceptions=True)
        await store.close()


class FakeScheduledScanner:
    def __init__(self):
        self.calls = []
        self.active = 0
        self.max_active = 0

    async def run_scope(self, chat_id):
        self.calls.append(chat_id)
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            await asyncio.sleep(0)
            if chat_id == -1002:
                raise ConnectionError("synthetic scheduled failure")
            if chat_id == -1003:
                raise DreamCycleBusyError("synthetic busy scope")
        finally:
            self.active -= 1


@pytest.mark.asyncio
async def test_scheduler_scans_enabled_scopes_with_bounded_concurrency(tmp_path):
    store = await AIStateRepository(tmp_path / "ai.db").connect()
    for chat_id in (-1001, -1002, -1003):
        await store.set_dream_memory_enabled(f"telegram:chat:{chat_id}", True)
    await store.set_dream_memory_enabled("telegram:chat:-1004", True)
    await store.set_continuous_memory_enabled(
        "telegram:chat:-1004",
        True,
        cursor_message_id=40,
    )
    scanner = FakeScheduledScanner()
    scheduler = DreamScheduler(
        scanner=scanner,
        store=store,
        settings=DreamSchedulerSettings(
            cron=None,
            concurrency=2,
            scope_batch_size=2,
        ),
    )
    try:
        result = await scheduler.run_once()
        assert result == DreamScheduleResult(
            scopes_seen=3,
            scopes_succeeded=1,
            scopes_failed=1,
            scopes_busy=1,
        )
        assert set(scanner.calls) == {-1001, -1002, -1003}
        assert scanner.max_active == 2
    finally:
        await store.close()


def test_scheduler_requires_valid_five_field_cron():
    with pytest.raises(ValueError, match="five-field"):
        DreamSchedulerSettings(cron="every hour")


class FakeContinuousScanner:
    def __init__(self):
        self.calls = []

    async def run_continuous_scope(self, chat_id):
        self.calls.append(chat_id)
        return type(
            "Result",
            (),
            {
                "messages_seen": 2,
                "messages_retained": 2,
                "documents_created": 1,
                "documents_unchanged": 0,
                "caught_up": chat_id != -1001,
            },
        )()


@pytest.mark.asyncio
async def test_continuous_scheduler_runs_only_continuous_scopes_and_reports_backlog(
    tmp_path,
):
    store = await AIStateRepository(tmp_path / "ai.db").connect()
    await store.set_continuous_memory_enabled(
        "telegram:chat:-1001",
        True,
        cursor_message_id=10,
    )
    await store.set_continuous_memory_enabled(
        "telegram:chat:-1002",
        True,
        cursor_message_id=20,
    )
    await store.set_dream_memory_enabled("telegram:chat:-1003", True)
    scanner = FakeContinuousScanner()
    scheduler = ContinuousMemoryScheduler(
        scanner=scanner,
        store=store,
        settings=ContinuousMemorySchedulerSettings(
            poll_interval_seconds=60,
            concurrency=2,
            scope_batch_size=2,
        ),
    )
    try:
        result = await scheduler.run_once()

        assert result == ContinuousMemoryScheduleResult(
            scopes_seen=2,
            scopes_succeeded=2,
            scopes_failed=0,
            scopes_busy=0,
            scopes_pending=1,
            messages_seen=4,
            messages_retained=4,
        )
        assert set(scanner.calls) == {-1001, -1002}
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_continuous_scheduler_immediately_drains_backlog(tmp_path):
    class CatchupScanner:
        def __init__(self):
            self.calls = 0
            self.caught_up = asyncio.Event()

        async def run_continuous_scope(self, chat_id):
            self.calls += 1
            caught_up = self.calls >= 2
            if caught_up:
                self.caught_up.set()
            return SimpleNamespace(
                messages_seen=1,
                messages_retained=1,
                documents_created=1,
                documents_unchanged=0,
                caught_up=caught_up,
            )

    store = await AIStateRepository(tmp_path / "ai.db").connect()
    await store.set_continuous_memory_enabled(
        "telegram:chat:-1001",
        True,
        cursor_message_id=10,
    )
    scanner = CatchupScanner()
    scheduler = ContinuousMemoryScheduler(
        scanner=scanner,
        store=store,
        settings=ContinuousMemorySchedulerSettings(
            poll_interval_seconds=3_600,
            concurrency=1,
            scope_batch_size=1,
        ),
    )
    try:
        scheduler.start()
        await asyncio.wait_for(scanner.caught_up.wait(), timeout=1)
        assert scanner.calls == 2
    finally:
        await scheduler.close()
        await store.close()


def test_dream_settings_load_temporal_session_limits(monkeypatch):
    monkeypatch.setenv("TELEFIRE_MEMORY_DREAM_SESSION_IDLE_SECONDS", "120")
    monkeypatch.setenv("TELEFIRE_MEMORY_DREAM_SESSION_MAX_SPAN_SECONDS", "600")
    monkeypatch.setenv("TELEFIRE_MEMORY_DREAM_SESSION_MAX_EVENTS", "12")
    monkeypatch.setenv("TELEFIRE_MEMORY_DREAM_SESSION_MAX_CHARS", "2048")
    monkeypatch.setenv("TELEFIRE_MEMORY_DREAM_SCOPE_TIMEOUT_SECONDS", "180")

    settings = DreamSettings.from_env()

    assert settings.session_idle_gap == timedelta(minutes=2)
    assert settings.session_max_span == timedelta(minutes=10)
    assert settings.session_max_events == 12
    assert settings.session_max_chars == 2_048
    assert settings.scope_timeout_seconds == 180


def test_continuous_scheduler_settings_load_from_environment(monkeypatch):
    monkeypatch.setenv("TELEFIRE_MEMORY_CONTINUOUS_POLL_SECONDS", "4.5")
    monkeypatch.setenv("TELEFIRE_MEMORY_CONTINUOUS_CONCURRENCY", "3")
    monkeypatch.setenv("TELEFIRE_MEMORY_CONTINUOUS_SCOPE_BATCH_SIZE", "12")

    settings = ContinuousMemorySchedulerSettings.from_env()

    assert settings.poll_interval_seconds == 4.5
    assert settings.concurrency == 3
    assert settings.scope_batch_size == 12


@pytest.mark.asyncio
async def test_partial_failure_keeps_cursor_and_retries_only_missing_document(tmp_path):
    first = FakeMessage(30, "First document")
    second = FakeMessage(31, "Second document")
    source = FakeSource([first, second])
    failing = FakeMemory(
        fail_document="telegram:dream-session:-1001:20260713T115500Z:31"
    )
    store, scanner = await make_scanner(
        tmp_path,
        source,
        failing,
        retain_concurrency=1,
    )
    try:
        with pytest.raises(ConnectionError, match="synthetic"):
            await scanner.run_scope(-1001)
        failed_state = await store.get_memory_dream_state("telegram:chat:-1001")
        assert failed_state.cursor_message_id == 30
        assert failed_state.scanned_until_at == first.date.timestamp()
        assert failed_state.last_success_at == NOW.timestamp()
        assert "ConnectionError" in failed_state.last_error

        healthy = FakeMemory()
        resumed = TelegramDreamScanner(
            source=source,
            store=store,
            memory=healthy,
            prompt_builder=PromptBuilder(identity_resolver=FakeIdentityResolver()),
            settings=scanner._settings,
            clock=lambda: NOW.timestamp(),
        )
        result = await resumed.run_scope(-1001)
        assert result.documents_created == 1
        assert result.documents_unchanged == 1
        assert [call["episode"].document_id for call in healthy.retain_calls] == [
            "telegram:dream-session:-1001:20260713T115500Z:31"
        ]
        succeeded = await store.get_memory_dream_state("telegram:chat:-1001")
        assert succeeded.cursor_message_id == 31
        assert succeeded.last_error is None
    finally:
        await store.close()
