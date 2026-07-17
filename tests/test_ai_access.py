import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest

from telefire.ai import (
    AIConversationHandler,
    AIRateLimiter,
    AIResponder,
    AIStateRepository,
    AgentEvent,
    AgentRunRequest,
    DirectoryPublicationTarget,
    PromptBuilder,
)
from telefire.ai_memory import MemoryRetainResult
from telefire.memory_directory import DirectorySource
from telefire.telegram.ai_identity import TELEGRAM_IDENTITY_CODEC


def actor_id(user_id: int) -> str:
    return TELEGRAM_IDENTITY_CODEC.actor_id(user_id)


class FakeAnswer:
    next_id = 100

    def __init__(self, text: str):
        self.id = self.__class__.next_id
        self.__class__.next_id += 1
        self.text = text
        self.edits = []

    async def edit(self, text: str, **kwargs):
        self.text = text
        self.edits.append(text)
        return self


class FakeMessage:
    next_id = 1

    def __init__(
        self,
        text: str,
        *,
        sender_id: int,
        reply_to=None,
        chat_id: int = -1001,
    ):
        self.id = self.__class__.next_id
        self.__class__.next_id += 1
        self.raw_text = text
        self.sender_id = sender_id
        self.chat_id = chat_id
        self.reply_to_msg_id = reply_to.id if reply_to else None
        self.date = datetime(2026, 7, 16, 12, 0, tzinfo=UTC)
        self._reply_to = reply_to
        self.replies = []
        self.deleted = False

    async def get_reply_message(self):
        return self._reply_to

    async def reply(self, text: str, **kwargs):
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


class BlockingGateway:
    def __init__(self):
        self.requests = []
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def run(self, request: AgentRunRequest) -> AsyncIterator[AgentEvent]:
        self.requests.append(request)
        self.started.set()
        await self.release.wait()
        session_id = request.session_id or f"session-{len(self.requests)}"
        yield AgentEvent(type="run_started", session_id=session_id)
        yield AgentEvent(type="text_delta", delta="done", reset=True)
        yield AgentEvent(
            type="run_completed",
            session_id=session_id,
            entry_id=f"entry-{len(self.requests)}",
            answer="done",
        )

    async def cancel(self, run_id: str) -> bool:
        self.release.set()
        return True


class FakeDirectoryMemory:
    def __init__(self, published=()):
        self.published = set(published)
        self.publications = []

    async def publish_directory(self, publication):
        self.publications.append(publication)
        self.published.add(publication.source.bank_id)
        return MemoryRetainResult(accepted=True)

    async def is_directory_source_published(self, bank_id):
        return bank_id in self.published


class FakeDirectorySourceResolver:
    def __init__(self, source):
        self.source = source
        self.publication_calls = []
        self.bank_calls = []

    async def resolve_publication(self, message, arguments):
        self.publication_calls.append((message, arguments))
        return DirectoryPublicationTarget(
            source=self.source,
            description="Owner supplied description",
        )

    async def resolve_bank(self, message, selector):
        self.bank_calls.append((message, selector))
        return self.source


async def make_handler(
    path,
    gateway,
    *,
    clock=lambda: 100.0,
    cooldown=30.0,
    memory=None,
    directory_source_resolver=None,
):
    store = await AIStateRepository(path).connect()
    limiter = AIRateLimiter(store, cooldown_seconds=cooldown, clock=clock)
    handler = AIConversationHandler(
        owner_id=10,
        responder=AIResponder(gateway),
        store=store,
        prompt_builder=PromptBuilder(
            identity_codec=TELEGRAM_IDENTITY_CODEC,
        ),
        rate_limiter=limiter,
        memory=memory,
        directory_source_resolver=directory_source_resolver,
        memory_command_delete_delay=0,
        identity_codec=TELEGRAM_IDENTITY_CODEC,
    )
    return handler, store


@pytest.fixture(autouse=True)
def reset_message_ids():
    FakeAnswer.next_id = 100
    FakeMessage.next_id = 1


@pytest.mark.asyncio
async def test_owner_can_allow_user_who_can_start_continue_and_fork(tmp_path):
    gateway = FakeGateway(["root", "continued", "forked"])
    handler, store = await make_handler(tmp_path / "state.db", gateway, cooldown=0)
    try:
        target = FakeMessage("hello", sender_id=20)
        allow = FakeMessage("/ai_allow", sender_id=10, reply_to=target)

        assert await handler.handle(allow) is True
        assert await store.is_allowed(actor_id(20)) is True
        assert allow.replies[0].text == "AI access allowed."
        assert allow.deleted is True

        trigger = FakeMessage("/ai root question", sender_id=20)
        assert await handler.handle(trigger) is True
        root_answer = trigger.replies[0]
        continuation = FakeMessage("continue", sender_id=20, reply_to=root_answer)
        assert await handler.handle(continuation) is True
        fork = FakeMessage("fork", sender_id=20, reply_to=root_answer)
        assert await handler.handle(fork) is True

        assert len(gateway.requests) == 3
        assert gateway.requests[2].prompt == "fork"
        assert gateway.requests[2].parent_entry_id == "entry-1"
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_owner_publishes_one_resolved_directory_source(tmp_path):
    source = DirectorySource(
        bank_id="telegram:chat:-100123",
        display_name="Coder Offtopic",
        platform="telegram",
        source_kind="group",
    )
    memory = FakeDirectoryMemory()
    resolver = FakeDirectorySourceResolver(source)
    handler, store = await make_handler(
        tmp_path / "state.db",
        FakeGateway([]),
        memory=memory,
        directory_source_resolver=resolver,
    )
    try:
        command = FakeMessage(
            "/ai_directory @CoderOfftopic owner notes",
            sender_id=10,
        )

        assert await handler.handle(command) is True

        assert resolver.publication_calls == [(command, "@CoderOfftopic owner notes")]
        assert len(memory.publications) == 1
        publication = memory.publications[0]
        assert publication.source == source
        assert publication.description == "Owner supplied description"
        assert publication.publisher_id == actor_id(10)
        assert publication.publication_id == f"telegram:message:-1001:{command.id}"
        assert command.replies[0].text == "Knowledge source published: Coder Offtopic."
        assert command.deleted is True
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_owner_grants_and_revokes_published_source_for_whitelisted_user(
    tmp_path,
):
    source = DirectorySource(
        bank_id="telegram:chat:-100123",
        display_name="Coder Offtopic",
        platform="telegram",
        source_kind="group",
    )
    memory = FakeDirectoryMemory((source.bank_id,))
    resolver = FakeDirectorySourceResolver(source)
    handler, store = await make_handler(
        tmp_path / "state.db",
        FakeGateway([]),
        memory=memory,
        directory_source_resolver=resolver,
    )
    try:
        target = FakeMessage("hello", sender_id=20)
        await store.allow_user(actor_id(20))
        allow = FakeMessage(
            "/ai_bank_allow @CoderOfftopic",
            sender_id=10,
            reply_to=target,
        )

        assert await handler.handle(allow) is True
        assert await store.list_bank_grants(actor_id(20)) == (source.bank_id,)
        assert (
            allow.replies[0].text == "Knowledge source access allowed: Coder Offtopic."
        )
        assert allow.deleted is True

        memory.published.clear()
        handler._memory = None
        deny = FakeMessage(
            "/ai_bank_deny @CoderOfftopic",
            sender_id=10,
            reply_to=target,
        )
        assert await handler.handle(deny) is True
        assert await store.list_bank_grants(actor_id(20)) == ()
        assert deny.replies[0].text == "Knowledge source access denied: Coder Offtopic."
        assert deny.deleted is True
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_cross_platform_grant_uses_only_published_canonical_bank_id(tmp_path):
    qq_bank = "qq:group:686743769"
    local_source = DirectorySource(
        bank_id="telegram:chat:-100123",
        display_name="Local Group",
        platform="telegram",
        source_kind="group",
    )
    memory = FakeDirectoryMemory((qq_bank,))
    resolver = FakeDirectorySourceResolver(local_source)
    handler, store = await make_handler(
        tmp_path / "state.db",
        FakeGateway([]),
        memory=memory,
        directory_source_resolver=resolver,
    )
    try:
        target = FakeMessage("hello", sender_id=20)
        await store.allow_user(actor_id(20))
        command = FakeMessage(
            f"/ai_bank_allow {qq_bank}",
            sender_id=10,
            reply_to=target,
        )

        assert await handler.handle(command) is True
        assert resolver.bank_calls == []
        assert await store.list_bank_grants(actor_id(20)) == (qq_bank,)
        assert command.replies[0].text == "Knowledge source access allowed."
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_grant_rejects_unpublished_source_and_unwhitelisted_user(tmp_path):
    source = DirectorySource(
        bank_id="telegram:chat:-100123",
        display_name="Coder Offtopic",
        platform="telegram",
        source_kind="group",
    )
    memory = FakeDirectoryMemory()
    resolver = FakeDirectorySourceResolver(source)
    handler, store = await make_handler(
        tmp_path / "state.db",
        FakeGateway([]),
        memory=memory,
        directory_source_resolver=resolver,
    )
    try:
        target = FakeMessage("hello", sender_id=20)
        unpublished = FakeMessage(
            "/ai_bank_allow @CoderOfftopic",
            sender_id=10,
            reply_to=target,
        )
        assert await handler.handle(unpublished) is True
        assert unpublished.replies[0].text == "Publish that knowledge source first."
        assert await store.list_bank_grants(actor_id(20)) == ()

        memory.published.add(source.bank_id)
        unwhitelisted = FakeMessage(
            "/ai_bank_allow @CoderOfftopic",
            sender_id=10,
            reply_to=target,
        )
        assert await handler.handle(unwhitelisted) is True
        assert unwhitelisted.replies[0].text == "Allow AI access for that user first."
        assert await store.list_bank_grants(actor_id(20)) == ()
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_unauthorized_and_revoked_users_are_silent(tmp_path):
    gateway = FakeGateway(["must not be called"])
    handler, store = await make_handler(tmp_path / "state.db", gateway)
    try:
        unauthorized = FakeMessage("/ai private", sender_id=20)
        assert await handler.handle(unauthorized) is False
        assert unauthorized.replies == []
        assert gateway.requests == []

        await store.allow_user(actor_id(20))
        target = FakeMessage("target", sender_id=20)
        deny = FakeMessage("/ai_deny", sender_id=10, reply_to=target)
        assert await handler.handle(deny) is True
        assert deny.replies[0].text == "AI access denied."
        assert deny.deleted is True

        revoked = FakeMessage("/ai private", sender_id=20)
        assert await handler.handle(revoked) is False
        assert revoked.replies == []
        assert gateway.requests == []
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_bank_grants_require_whitelist_and_survive_restart(tmp_path):
    path = tmp_path / "state.db"
    actor = actor_id(20)
    telegram_bank = "telegram:chat:-100123"
    qq_bank = "qq:group:686743769"
    store = await AIStateRepository(path).connect()
    try:
        assert await store.grant_bank(actor, telegram_bank) is False
        await store.allow_user(actor)
        assert await store.grant_bank(actor, telegram_bank) is True
        assert await store.grant_bank(actor, qq_bank) is True
        assert await store.grant_bank(actor, telegram_bank) is True
        assert await store.list_bank_grants(actor) == (qq_bank, telegram_bank)
    finally:
        await store.close()

    restarted = await AIStateRepository(path).connect()
    try:
        assert await restarted.list_bank_grants(actor) == (qq_bank, telegram_bank)
        await restarted.revoke_bank(actor, telegram_bank)
        assert await restarted.list_bank_grants(actor) == (qq_bank,)
    finally:
        await restarted.close()


@pytest.mark.asyncio
async def test_deny_atomically_clears_usage_and_all_bank_grants(tmp_path):
    path = tmp_path / "state.db"
    actor = actor_id(20)
    store = await AIStateRepository(path).connect()
    try:
        await store.allow_user(actor)
        await store.set_last_request_at(actor, 123.0)
        assert await store.grant_bank(actor, "telegram:chat:-100123") is True
        assert await store.grant_bank(actor, "qq:group:686743769") is True

        await store.deny_user(actor)

        assert await store.is_allowed(actor) is False
        assert await store.get_last_request_at(actor) is None
        assert await store.list_bank_grants(actor) == ()

        await store.allow_user(actor)
        assert await store.list_bank_grants(actor) == ()
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_nonowner_has_one_inflight_request_and_persistent_cooldown(tmp_path):
    now = [100.0]
    gateway = BlockingGateway()
    handler, store = await make_handler(
        tmp_path / "state.db",
        gateway,
        clock=lambda: now[0],
    )
    await store.allow_user(actor_id(20))
    try:
        first = FakeMessage("/ai first", sender_id=20)
        first_task = asyncio.create_task(handler.handle(first))
        await gateway.started.wait()

        concurrent = FakeMessage("/ai concurrent", sender_id=20)
        assert await handler.handle(concurrent) is True
        assert concurrent.replies[0].text == "AI rate limit active. Try again shortly."
        assert len(gateway.requests) == 1

        gateway.release.set()
        assert await first_task is True

        cooldown = FakeMessage("/ai cooldown", sender_id=20)
        assert await handler.handle(cooldown) is True
        assert cooldown.replies[0].text == "AI rate limit active. Try again shortly."
        assert len(gateway.requests) == 1
    finally:
        await store.close()

    restarted = await AIStateRepository(tmp_path / "state.db").connect()
    try:
        restarted_now = [110.0]
        limiter = AIRateLimiter(
            restarted,
            cooldown_seconds=30,
            clock=lambda: restarted_now[0],
        )
        assert await restarted.is_allowed(actor_id(20)) is True
        assert await limiter.acquire(actor_id=actor_id(20), is_owner=False) is False
        restarted_now[0] = 131.0
        assert await limiter.acquire(actor_id=actor_id(20), is_owner=False) is True
        await limiter.release(actor_id=actor_id(20), is_owner=False)
        await restarted.deny_user(actor_id(20))
    finally:
        await restarted.close()

    final_store = await AIStateRepository(tmp_path / "state.db").connect()
    try:
        assert await final_store.is_allowed(actor_id(20)) is False
    finally:
        await final_store.close()


@pytest.mark.asyncio
async def test_owner_is_exempt_and_never_added_to_whitelist(tmp_path):
    gateway = BlockingGateway()
    handler, store = await make_handler(tmp_path / "state.db", gateway)
    try:
        owner_message = FakeMessage("owner", sender_id=10)
        allow_owner = FakeMessage("/ai_allow", sender_id=10, reply_to=owner_message)
        assert await handler.handle(allow_owner) is True
        assert allow_owner.replies[0].text == "Owner access is always enabled."
        assert allow_owner.deleted is True
        assert await store.is_allowed(actor_id(10)) is False

        first = FakeMessage("/ai first", sender_id=10)
        second = FakeMessage("/ai second", sender_id=10)
        tasks = [
            asyncio.create_task(handler.handle(first)),
            asyncio.create_task(handler.handle(second)),
        ]
        while len(gateway.requests) < 2:
            await asyncio.sleep(0)
        gateway.release.set()
        assert await asyncio.gather(*tasks) == [True, True]
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_nonowner_access_command_is_not_executed_or_deleted(tmp_path):
    handler, store = await make_handler(
        tmp_path / "state.db",
        FakeGateway(["must not be called"]),
    )
    try:
        target = FakeMessage("hello", sender_id=30)
        command = FakeMessage("/ai_allow", sender_id=20, reply_to=target)

        assert await handler.handle(command) is False
        assert command.deleted is False
        assert command.replies == []
        assert await store.is_allowed(actor_id(30)) is False
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_owner_access_usage_error_remains_visible(tmp_path):
    handler, store = await make_handler(
        tmp_path / "state.db",
        FakeGateway(["must not be called"]),
    )
    try:
        command = FakeMessage("/ai_allow", sender_id=10)

        assert await handler.handle(command) is True
        assert command.replies[0].text == "Usage: reply to a user with /ai_allow"
        assert command.deleted is False
    finally:
        await store.close()
