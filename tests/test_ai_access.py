import asyncio
from collections.abc import AsyncIterator

import pytest

from telefire.ai import (
    AIConversationHandler,
    AIRateLimiter,
    AIResponder,
    AIStateRepository,
    AgentEvent,
    AgentRunRequest,
    PromptBuilder,
)
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


async def make_handler(path, gateway, *, clock=lambda: 100.0, cooldown=30.0):
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
