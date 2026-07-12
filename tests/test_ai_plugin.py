import asyncio
from types import SimpleNamespace

import pytest
from telethon.tl import functions as telegram_functions
from telethon.tl import types as telegram_types

from telefire.plugins.ai import TelegramAI


class FailingHandler:
    async def handle(self, message):
        raise RuntimeError("handler failed")


class RecordingLogger:
    def __init__(self):
        self.exceptions = []
        self.infos = []
        self.warnings = []

    def exception(self, message, *args):
        self.exceptions.append((message, args))

    def info(self, message, *args):
        self.infos.append((message, args))

    def warning(self, message, *args):
        self.warnings.append((message, args))


class RecordingHandler:
    def __init__(self, *, remembered=True):
        self.remembered = remembered
        self.memory_targets = []
        self.messages = []

    async def remember_reply_chain(self, message):
        self.memory_targets.append(message)
        return self.remembered

    async def handle(self, message):
        self.messages.append(message)
        return False


class FakeStateRepository:
    def __init__(self):
        self.processed = set()
        self.records = []

    async def is_memory_forward_processed(self, *, owner_id, saved_message_id):
        return (owner_id, saved_message_id) in self.processed

    async def record_memory_forward(
        self,
        *,
        owner_id,
        saved_message_id,
        source_chat_id,
        source_message_id,
    ):
        self.processed.add((owner_id, saved_message_id))
        self.records.append(
            (owner_id, saved_message_id, source_chat_id, source_message_id)
        )


class FakeTelegramClient:
    def __init__(self, source_message, *, request_error=None):
        self.source_message = source_message
        self.request_error = request_error
        self.get_messages_calls = []
        self.requests = []

    async def get_messages(self, peer, *, ids):
        self.get_messages_calls.append((peer, ids))
        return self.source_message

    async def __call__(self, request):
        self.requests.append(request)
        if self.request_error is not None:
            raise self.request_error
        return SimpleNamespace()


class FakeSavedMessage:
    def __init__(
        self,
        *,
        message_id=77,
        owner_id=10,
        source_peer=None,
        source_message_id=42,
        forwarded=True,
    ):
        self.id = message_id
        self.peer_id = telegram_types.PeerUser(owner_id)
        self.chat_id = owner_id
        self.sender_id = owner_id
        self.out = True
        self.fwd_from = (
            SimpleNamespace(
                saved_from_peer=source_peer,
                saved_from_msg_id=source_message_id,
            )
            if forwarded
            else None
        )
        self.replies = []

    async def get_input_chat(self):
        return telegram_types.InputPeerSelf()

    async def reply(self, text, **kwargs):
        self.replies.append((text, kwargs))
        return SimpleNamespace()


def make_plugin(*, handler, store, client, owner_id=10):
    plugin = TelegramAI.__new__(TelegramAI)
    plugin._handler = handler
    plugin._store = store
    plugin._owner_id = owner_id
    plugin._saved_memory_lock = asyncio.Lock()
    plugin.service = SimpleNamespace(client=client)
    plugin.logger = RecordingLogger()
    return plugin


@pytest.mark.asyncio
async def test_ai_plugin_logs_message_handler_failures():
    plugin = TelegramAI.__new__(TelegramAI)
    plugin._handler = FailingHandler()
    plugin._owner_id = 10
    plugin.logger = RecordingLogger()
    event = SimpleNamespace(
        message=SimpleNamespace(chat_id=-1001, id=42),
    )

    await plugin._on_message(event)

    assert plugin.logger.exceptions == [
        (
            "Telegram AI message handling failed (chat_id=%s, message_id=%s)",
            (-1001, 42),
        )
    ]


@pytest.mark.asyncio
async def test_saved_messages_forward_ingests_original_chain_and_records_success():
    source_peer = telegram_types.PeerChannel(1001)
    source_message = SimpleNamespace(chat_id=-1001001, id=42)
    saved_message = FakeSavedMessage(source_peer=source_peer)
    handler = RecordingHandler()
    store = FakeStateRepository()
    client = FakeTelegramClient(source_message)
    plugin = make_plugin(handler=handler, store=store, client=client)

    await plugin._on_message(SimpleNamespace(message=saved_message))

    assert client.get_messages_calls == [(source_peer, 42)]
    assert handler.memory_targets == [source_message]
    assert handler.messages == []
    assert store.records == [(10, 77, -1001001, 42)]
    assert len(client.requests) == 1
    marker = client.requests[0]
    assert isinstance(marker, telegram_functions.messages.SendReactionRequest)
    assert [reaction.emoticon for reaction in marker.reaction] == ["✍"]


@pytest.mark.asyncio
async def test_cross_session_saved_forward_uses_raw_self_peer_identity():
    source_peer = telegram_types.PeerChannel(1001)
    source_message = SimpleNamespace(chat_id=-1001001, id=42)
    saved_message = FakeSavedMessage(source_peer=source_peer)
    saved_message.chat_id = -1001001
    saved_message.sender_id = None
    saved_message.out = False
    handler = RecordingHandler()
    store = FakeStateRepository()
    client = FakeTelegramClient(source_message)
    plugin = make_plugin(handler=handler, store=store, client=client)

    await plugin._on_message(SimpleNamespace(message=saved_message))

    assert handler.memory_targets == [source_message]
    assert store.records == [(10, 77, -1001001, 42)]


@pytest.mark.asyncio
async def test_saved_messages_forward_receipt_prevents_duplicate_ingestion():
    source_peer = telegram_types.PeerChannel(1001)
    source_message = SimpleNamespace(chat_id=-1001001, id=42)
    saved_message = FakeSavedMessage(source_peer=source_peer)
    handler = RecordingHandler()
    store = FakeStateRepository()
    client = FakeTelegramClient(source_message)
    plugin = make_plugin(handler=handler, store=store, client=client)

    event = SimpleNamespace(message=saved_message)
    await plugin._on_message(event)
    await plugin._on_message(event)

    assert client.get_messages_calls == [(source_peer, 42)]
    assert handler.memory_targets == [source_message]
    assert store.records == [(10, 77, -1001001, 42)]


@pytest.mark.asyncio
async def test_failed_saved_messages_ingest_is_marked_but_not_recorded():
    source_peer = telegram_types.PeerChannel(1001)
    source_message = SimpleNamespace(chat_id=-1001001, id=42)
    saved_message = FakeSavedMessage(source_peer=source_peer)
    handler = RecordingHandler(remembered=False)
    store = FakeStateRepository()
    client = FakeTelegramClient(source_message)
    plugin = make_plugin(handler=handler, store=store, client=client)

    await plugin._on_message(SimpleNamespace(message=saved_message))

    assert handler.memory_targets == [source_message]
    assert store.records == []
    assert [reaction.emoticon for reaction in client.requests[0].reaction] == ["👎"]
    assert saved_message.replies == [
        (
            "Memory update failed. Forward the message again to retry.",
            {"parse_mode": None},
        )
    ]


@pytest.mark.asyncio
async def test_failed_ingest_replies_privately_when_reactions_are_unavailable():
    source_peer = telegram_types.PeerChannel(1001)
    source_message = SimpleNamespace(chat_id=-1001001, id=42)
    saved_message = FakeSavedMessage(source_peer=source_peer)
    handler = RecordingHandler(remembered=False)
    store = FakeStateRepository()
    client = FakeTelegramClient(
        source_message,
        request_error=RuntimeError("premium required"),
    )
    plugin = make_plugin(handler=handler, store=store, client=client)

    await plugin._on_message(SimpleNamespace(message=saved_message))

    assert store.records == []
    assert saved_message.replies == [
        (
            "Memory update failed. Forward the message again to retry.",
            {"parse_mode": None},
        )
    ]


@pytest.mark.asyncio
async def test_success_remains_silent_when_reactions_are_unavailable():
    source_peer = telegram_types.PeerChannel(1001)
    source_message = SimpleNamespace(chat_id=-1001001, id=42)
    saved_message = FakeSavedMessage(source_peer=source_peer)
    handler = RecordingHandler()
    store = FakeStateRepository()
    client = FakeTelegramClient(
        source_message,
        request_error=RuntimeError("premium required"),
    )
    plugin = make_plugin(handler=handler, store=store, client=client)

    await plugin._on_message(SimpleNamespace(message=saved_message))

    assert store.records == [(10, 77, -1001001, 42)]
    assert saved_message.replies == []


@pytest.mark.asyncio
async def test_unresolvable_saved_forward_is_not_treated_as_an_ai_command():
    saved_message = FakeSavedMessage(source_peer=None)
    handler = RecordingHandler()
    store = FakeStateRepository()
    client = FakeTelegramClient(None)
    plugin = make_plugin(handler=handler, store=store, client=client)

    await plugin._on_message(SimpleNamespace(message=saved_message))

    assert handler.memory_targets == []
    assert handler.messages == []
    assert store.records == []
    assert [reaction.emoticon for reaction in client.requests[0].reaction] == ["👎"]
    assert saved_message.replies == [
        (
            "Memory update unavailable: Telegram did not expose the original "
            "message, so its reply chain cannot be traced.",
            {"parse_mode": None},
        )
    ]


@pytest.mark.asyncio
async def test_ordinary_message_still_uses_ai_conversation_handler():
    message = FakeSavedMessage(forwarded=False)
    handler = RecordingHandler()
    store = FakeStateRepository()
    client = FakeTelegramClient(None)
    plugin = make_plugin(handler=handler, store=store, client=client)

    await plugin._on_message(SimpleNamespace(message=message))

    assert handler.memory_targets == []
    assert handler.messages == [message]
    assert client.requests == []
