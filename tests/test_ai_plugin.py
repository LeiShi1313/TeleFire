import asyncio
from types import SimpleNamespace

import pytest
from telethon import utils as telegram_utils
from telethon.tl import functions as telegram_functions
from telethon.tl import types as telegram_types

from telefire.plugins.ai import TelegramAI, _parse_telegram_message_link


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


class BlockingRecordingHandler(RecordingHandler):
    def __init__(self):
        super().__init__()
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def remember_reply_chain(self, message):
        self.memory_targets.append(message)
        self.started.set()
        await self.release.wait()
        return True


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
    def __init__(
        self,
        source_message,
        *,
        input_peer=None,
        dialogs=(),
        resolution_error=None,
        request_error=None,
    ):
        self.source_message = source_message
        self.input_peer = input_peer
        self.dialogs = dialogs
        self.resolution_error = resolution_error
        self.request_error = request_error
        self.get_input_entity_calls = []
        self.get_messages_calls = []
        self.requests = []

    async def get_input_entity(self, peer):
        self.get_input_entity_calls.append(peer)
        if self.resolution_error is not None:
            raise self.resolution_error
        return self.input_peer if self.input_peer is not None else peer

    async def get_messages(self, peer, *, ids):
        self.get_messages_calls.append((peer, ids))
        return self.source_message

    async def iter_dialogs(self):
        for dialog in self.dialogs:
            yield dialog

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
        text="",
    ):
        self.id = message_id
        self.peer_id = telegram_types.PeerUser(owner_id)
        self.chat_id = owner_id
        self.sender_id = owner_id
        self.out = True
        self.raw_text = text
        self.fwd_from = (
            SimpleNamespace(
                saved_from_peer=source_peer,
                saved_from_msg_id=source_message_id,
            )
            if forwarded
            else None
        )
        self.replies = []
        self.reply_messages = []

    async def get_input_chat(self):
        return telegram_types.InputPeerSelf()

    async def reply(self, text, **kwargs):
        self.replies.append((text, kwargs))
        reply_message = FakeReplyMessage(text)
        self.reply_messages.append(reply_message)
        return reply_message


class FakeReplyMessage:
    def __init__(self, text):
        self.text = text
        self.edits = []

    async def edit(self, text, **kwargs):
        self.text = text
        self.edits.append((text, kwargs))
        return self


class RecordingEditLimiter:
    def __init__(self):
        self.waits = []

    async def run(self, operation, *, wait):
        self.waits.append(wait)
        await operation()
        return True


def make_plugin(*, handler, store, client, owner_id=10, edit_limiter=None):
    plugin = TelegramAI.__new__(TelegramAI)
    plugin._handler = handler
    plugin._store = store
    plugin._owner_id = owner_id
    plugin._saved_memory_lock = asyncio.Lock()
    plugin._edit_limiter = edit_limiter or RecordingEditLimiter()
    plugin.service = SimpleNamespace(client=client)
    plugin.logger = RecordingLogger()
    return plugin


def assert_saved_memory_status(message, final_text):
    assert message.replies == [("Remembering...", {"parse_mode": None})]
    assert len(message.reply_messages) == 1
    assert message.reply_messages[0].text == final_text
    assert message.reply_messages[0].edits == [(final_text, {"parse_mode": None})]


@pytest.mark.parametrize(
    ("text", "username", "channel_id", "message_id"),
    [
        ("https://t.me/public_group/42", "public_group", None, 42),
        ("https://t.me/public_group/7/42", "public_group", None, 42),
        ("https://t.me/c/1001/42", None, 1001, 42),
        ("https://t.me/c/1001/7/42?single", None, 1001, 42),
        ("  https://t.me/c/1001/42?thread=7  ", None, 1001, 42),
    ],
)
def test_parse_telegram_message_link(
    text,
    username,
    channel_id,
    message_id,
):
    parsed = _parse_telegram_message_link(text)

    assert parsed is not None
    assert parsed.username == username
    assert parsed.channel_id == channel_id
    assert parsed.message_id == message_id


@pytest.mark.parametrize(
    "text",
    [
        "remember https://t.me/public_group/42",
        "https://t.me/public_group/\n42",
        "https://example.com/public_group/42",
        "http://t.me/public_group/42",
        "https://t.me/public_group/not-a-message",
        "https://t.me/public_group/7/not-a-message",
        "https://t.me/c/1001/not-a-message",
        "https://t.me/c/1001/7/42?comment=99",
        "https://t.me/public_group/42?COMMENT=99",
        "https://t.me/c/1001/7/8/42",
    ],
)
def test_parse_telegram_message_link_rejects_non_message_links(text):
    assert _parse_telegram_message_link(text) is None


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
async def test_saved_messages_private_link_ingests_original_chain():
    source_peer = telegram_types.PeerChannel(1001)
    source_chat_id = telegram_utils.get_peer_id(source_peer)
    source_message = SimpleNamespace(chat_id=source_chat_id, id=42)
    saved_message = FakeSavedMessage(
        forwarded=False,
        text="https://t.me/c/1001/7/42?single",
    )
    handler = RecordingHandler()
    store = FakeStateRepository()
    client = FakeTelegramClient(source_message, input_peer=source_peer)
    plugin = make_plugin(handler=handler, store=store, client=client)

    await plugin._on_message(SimpleNamespace(message=saved_message))

    assert client.get_input_entity_calls == [telegram_types.PeerChannel(1001)]
    assert client.get_messages_calls == [(source_peer, 42)]
    assert handler.memory_targets == [source_message]
    assert handler.messages == []
    assert store.records == [(10, 77, source_chat_id, 42)]
    assert [reaction.emoticon for reaction in client.requests[0].reaction] == ["✍"]


@pytest.mark.asyncio
async def test_saved_messages_public_link_resolves_channel_and_ingests_chain():
    source_peer = telegram_types.PeerChannel(1001)
    source_chat_id = telegram_utils.get_peer_id(source_peer)
    source_message = SimpleNamespace(chat_id=source_chat_id, id=42)
    saved_message = FakeSavedMessage(
        forwarded=False,
        text="https://t.me/public_group/42",
    )
    handler = RecordingHandler()
    store = FakeStateRepository()
    client = FakeTelegramClient(source_message, input_peer=source_peer)
    plugin = make_plugin(handler=handler, store=store, client=client)

    await plugin._on_message(SimpleNamespace(message=saved_message))

    assert client.get_input_entity_calls == ["public_group"]
    assert client.get_messages_calls == [(source_peer, 42)]
    assert handler.memory_targets == [source_message]
    assert store.records == [(10, 77, source_chat_id, 42)]


@pytest.mark.asyncio
async def test_private_link_finds_uncached_channel_in_dialogs():
    source_peer = telegram_types.PeerChannel(1001)
    source_chat_id = telegram_utils.get_peer_id(source_peer)
    source_message = SimpleNamespace(chat_id=source_chat_id, id=42)
    saved_message = FakeSavedMessage(
        forwarded=False,
        text="https://t.me/c/1001/42",
    )
    handler = RecordingHandler()
    store = FakeStateRepository()
    client = FakeTelegramClient(
        source_message,
        dialogs=[SimpleNamespace(id=source_chat_id, input_entity=source_peer)],
        resolution_error=ValueError("entity is not in the session cache"),
    )
    plugin = make_plugin(handler=handler, store=store, client=client)

    await plugin._on_message(SimpleNamespace(message=saved_message))

    assert client.get_input_entity_calls == [telegram_types.PeerChannel(1001)]
    assert client.get_messages_calls == [(source_peer, 42)]
    assert handler.memory_targets == [source_message]
    assert store.records == [(10, 77, source_chat_id, 42)]


@pytest.mark.asyncio
async def test_saved_messages_link_receipt_prevents_duplicate_ingestion():
    source_peer = telegram_types.PeerChannel(1001)
    source_chat_id = telegram_utils.get_peer_id(source_peer)
    source_message = SimpleNamespace(chat_id=source_chat_id, id=42)
    saved_message = FakeSavedMessage(
        forwarded=False,
        text="https://t.me/c/1001/42",
    )
    handler = RecordingHandler()
    store = FakeStateRepository()
    client = FakeTelegramClient(source_message, input_peer=source_peer)
    plugin = make_plugin(handler=handler, store=store, client=client)

    event = SimpleNamespace(message=saved_message)
    await plugin._on_message(event)
    await plugin._on_message(event)

    assert client.get_messages_calls == [(source_peer, 42)]
    assert handler.memory_targets == [source_message]
    assert store.records == [(10, 77, source_chat_id, 42)]


@pytest.mark.asyncio
async def test_inaccessible_saved_messages_link_is_not_recorded():
    saved_message = FakeSavedMessage(
        forwarded=False,
        text="https://t.me/c/1001/42",
    )
    handler = RecordingHandler()
    store = FakeStateRepository()
    client = FakeTelegramClient(
        None,
        resolution_error=ValueError("entity is not in the session cache"),
    )
    plugin = make_plugin(handler=handler, store=store, client=client)

    await plugin._on_message(SimpleNamespace(message=saved_message))

    assert handler.memory_targets == []
    assert handler.messages == []
    assert store.records == []
    assert [reaction.emoticon for reaction in client.requests[0].reaction] == ["👎"]
    assert_saved_memory_status(
        saved_message,
        "Memory update unavailable: the linked message could not be fetched. "
        "Make sure this account can open it and the message still exists.",
    )


@pytest.mark.asyncio
async def test_saved_messages_link_rejects_message_from_another_chat():
    source_peer = telegram_types.PeerChannel(1001)
    wrong_chat_id = telegram_utils.get_peer_id(telegram_types.PeerChannel(2002))
    source_message = SimpleNamespace(chat_id=wrong_chat_id, id=42)
    saved_message = FakeSavedMessage(
        forwarded=False,
        text="https://t.me/c/1001/42",
    )
    handler = RecordingHandler()
    store = FakeStateRepository()
    client = FakeTelegramClient(source_message, input_peer=source_peer)
    plugin = make_plugin(handler=handler, store=store, client=client)

    await plugin._on_message(SimpleNamespace(message=saved_message))

    assert handler.memory_targets == []
    assert store.records == []
    assert_saved_memory_status(
        saved_message,
        "Memory update unavailable: the linked message could not be fetched. "
        "Make sure this account can open it and the message still exists.",
    )


@pytest.mark.asyncio
async def test_saved_messages_link_rejects_different_message_id():
    source_peer = telegram_types.PeerChannel(1001)
    source_chat_id = telegram_utils.get_peer_id(source_peer)
    source_message = SimpleNamespace(chat_id=source_chat_id, id=99)
    saved_message = FakeSavedMessage(
        forwarded=False,
        text="https://t.me/c/1001/42",
    )
    handler = RecordingHandler()
    store = FakeStateRepository()
    client = FakeTelegramClient(source_message, input_peer=source_peer)
    plugin = make_plugin(handler=handler, store=store, client=client)

    await plugin._on_message(SimpleNamespace(message=saved_message))

    assert handler.memory_targets == []
    assert store.records == []
    assert_saved_memory_status(
        saved_message,
        "Memory update unavailable: the linked message could not be fetched. "
        "Make sure this account can open it and the message still exists.",
    )


@pytest.mark.asyncio
async def test_failed_saved_messages_link_ingest_uses_link_retry_instruction():
    source_peer = telegram_types.PeerChannel(1001)
    source_chat_id = telegram_utils.get_peer_id(source_peer)
    source_message = SimpleNamespace(chat_id=source_chat_id, id=42)
    saved_message = FakeSavedMessage(
        forwarded=False,
        text="https://t.me/c/1001/42",
    )
    handler = RecordingHandler(remembered=False)
    store = FakeStateRepository()
    client = FakeTelegramClient(source_message, input_peer=source_peer)
    plugin = make_plugin(handler=handler, store=store, client=client)

    await plugin._on_message(SimpleNamespace(message=saved_message))

    assert handler.memory_targets == [source_message]
    assert store.records == []
    assert_saved_memory_status(
        saved_message,
        "Memory update failed. Send the message link again to retry.",
    )


@pytest.mark.asyncio
async def test_saved_messages_note_containing_link_stays_an_ordinary_message():
    saved_message = FakeSavedMessage(
        forwarded=False,
        text="Reference: https://t.me/c/1001/42",
    )
    handler = RecordingHandler()
    store = FakeStateRepository()
    client = FakeTelegramClient(None)
    plugin = make_plugin(handler=handler, store=store, client=client)

    await plugin._on_message(SimpleNamespace(message=saved_message))

    assert handler.memory_targets == []
    assert handler.messages == [saved_message]
    assert client.get_input_entity_calls == []
    assert client.requests == []


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
    assert_saved_memory_status(
        saved_message,
        "Memory update failed. Forward the message again to retry.",
    )


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
    assert_saved_memory_status(
        saved_message,
        "Memory update failed. Forward the message again to retry.",
    )


@pytest.mark.asyncio
async def test_success_reports_status_when_reactions_are_unavailable():
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
    assert_saved_memory_status(saved_message, "Remembered.")


@pytest.mark.asyncio
async def test_saved_memory_shows_processing_then_success_without_reactions():
    source_peer = telegram_types.PeerChannel(1001)
    source_message = SimpleNamespace(chat_id=-1001001, id=42)
    saved_message = FakeSavedMessage(source_peer=source_peer)
    handler = BlockingRecordingHandler()
    store = FakeStateRepository()
    client = FakeTelegramClient(
        source_message,
        request_error=RuntimeError("premium required"),
    )
    plugin = make_plugin(handler=handler, store=store, client=client)

    ingest = asyncio.create_task(
        plugin._on_message(SimpleNamespace(message=saved_message))
    )
    await handler.started.wait()

    assert saved_message.replies == [("Remembering...", {"parse_mode": None})]
    assert saved_message.reply_messages[0].text == "Remembering..."

    handler.release.set()
    await ingest

    assert saved_message.reply_messages[0].text == "Remembered."
    assert saved_message.reply_messages[0].edits == [
        ("Remembered.", {"parse_mode": None})
    ]


@pytest.mark.asyncio
async def test_saved_memory_completion_uses_the_account_edit_limiter():
    source_peer = telegram_types.PeerChannel(1001)
    source_message = SimpleNamespace(chat_id=-1001001, id=42)
    saved_message = FakeSavedMessage(source_peer=source_peer)
    edit_limiter = RecordingEditLimiter()
    plugin = make_plugin(
        handler=RecordingHandler(),
        store=FakeStateRepository(),
        client=FakeTelegramClient(source_message),
        edit_limiter=edit_limiter,
    )

    await plugin._on_message(SimpleNamespace(message=saved_message))

    assert edit_limiter.waits == [True]
    assert saved_message.reply_messages[0].text == "Remembered."


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
    assert_saved_memory_status(
        saved_message,
        "Telegram hid the original source. Paste the original message link "
        "in Saved Messages to remember its reply chain.",
    )


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
