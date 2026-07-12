from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import pytest
from telethon.errors import MessageNotModifiedError

from telefire.ai import (
    AIAnswerMarker,
    AIConversationHandler,
    AIResponder,
    AgentEvent,
    AgentRunRequest,
    PromptBuilder,
)


class FakeAnswer:
    next_id = 100

    def __init__(self, text: str, chat_id: int, reply_to_msg_id: int):
        self.id = self.__class__.next_id
        self.__class__.next_id += 1
        self.text = text
        self.chat_id = chat_id
        self.reply_to_msg_id = reply_to_msg_id
        self.edits: list[str] = []

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
        sender_id: int = 10,
        chat_id: int = -1001,
        reply_to=None,
    ):
        self.id = self.__class__.next_id
        self.__class__.next_id += 1
        self.raw_text = text
        self.sender_id = sender_id
        self.chat_id = chat_id
        self.reply_to_msg_id = reply_to.id if reply_to else None
        self._reply_to = reply_to
        self.replies: list[FakeAnswer] = []

    async def get_reply_message(self):
        return self._reply_to

    async def reply(self, text: str, **kwargs):
        answer = FakeAnswer(text, self.chat_id, self.id)
        self.replies.append(answer)
        return answer


class FakeAgentGateway:
    def __init__(self, answers: list[str] | None = None):
        self.answers = iter(answers or [])
        self.requests: list[AgentRunRequest] = []
        self.cancelled: list[str] = []

    async def run(self, request: AgentRunRequest) -> AsyncIterator[AgentEvent]:
        self.requests.append(request)
        answer = next(self.answers)
        session = request.session_id or f"session-{len(self.requests)}"
        yield AgentEvent(
            type="run_started",
            run_id=request.run_id,
            session_id=session,
        )
        yield AgentEvent(type="text_delta", delta=answer, reset=True)
        yield AgentEvent(
            type="run_completed",
            session_id=session,
            entry_id=f"entry-{len(self.requests)}",
            answer=answer,
        )

    async def cancel(self, run_id: str) -> bool:
        self.cancelled.append(run_id)
        return True


class BlockingAgentGateway(FakeAgentGateway):
    def __init__(self):
        super().__init__()
        self.started = asyncio.Event()
        self.cancelled_event = asyncio.Event()

    async def run(self, request: AgentRunRequest) -> AsyncIterator[AgentEvent]:
        self.requests.append(request)
        self.started.set()
        yield AgentEvent(
            type="run_started",
            run_id=request.run_id,
            session_id="session-blocking",
        )
        await self.cancelled_event.wait()
        yield AgentEvent(
            type="run_failed",
            code="CANCELLED",
            message="Agent run cancelled",
        )

    async def cancel(self, run_id: str) -> bool:
        self.cancelled.append(run_id)
        self.cancelled_event.set()
        return True


class FakeStore:
    def __init__(self, allowed: set[int] | None = None):
        self.allowed = allowed or set()
        self.markers: dict[tuple[int, int], AIAnswerMarker] = {}

    async def get_answer(self, chat_id, answer_message_id):
        return self.markers.get((chat_id, answer_message_id))

    async def save_answer(self, marker):
        self.markers[(marker.chat_id, marker.answer_message_id)] = marker

    async def is_allowed(self, user_id):
        return user_id in self.allowed

    async def get_last_request_at(self, user_id):
        return None

    async def set_last_request_at(self, user_id, timestamp):
        return None

    async def allow_user(self, user_id):
        self.allowed.add(user_id)

    async def deny_user(self, user_id):
        self.allowed.discard(user_id)


@pytest.fixture(autouse=True)
def reset_ids():
    FakeAnswer.next_id = 100
    FakeMessage.next_id = 1


@pytest.mark.asyncio
async def test_tool_snapshot_is_replaced_by_the_streamed_final_answer():
    class SnapshotGateway(FakeAgentGateway):
        async def run(self, request):
            yield AgentEvent(
                type="run_started",
                run_id=request.run_id,
                session_id="session-1",
            )
            yield AgentEvent(
                type="tool_snapshot",
                phase="started",
                tool="web_search",
                summary="Searching web: current release",
            )
            yield AgentEvent(type="text_delta", delta="<b>Final", reset=True)
            yield AgentEvent(type="text_delta", delta=" answer</b>", reset=False)
            yield AgentEvent(
                type="run_completed",
                session_id="session-1",
                entry_id="entry-1",
                answer="<b>Final answer</b>",
            )

    responder = AIResponder(SnapshotGateway(), edit_cadence=0)
    trigger = FakeMessage("/ai search")
    request = AgentRunRequest(
        run_id="11111111-1111-4111-8111-111111111111",
        session_id=None,
        parent_entry_id=None,
        prompt="search",
        context=(),
        system_prompt=PromptBuilder().system_prompt,
        tool_policy="owner",
    )

    result = await responder.answer(trigger, request)

    answer = trigger.replies[0]
    assert any("Searching web" in edit for edit in answer.edits)
    assert answer.text == "Final answer"
    assert "Searching web" not in answer.text
    assert result.session_id == "session-1"
    assert result.entry_id == "entry-1"


@pytest.mark.asyncio
async def test_repeated_tool_snapshot_does_not_fail_the_agent_run():
    class TelegramLikeAnswer(FakeAnswer):
        async def edit(self, text: str, **kwargs):
            if text == self.text:
                raise MessageNotModifiedError(request=None)
            return await super().edit(text, **kwargs)

    class TelegramLikeMessage(FakeMessage):
        async def reply(self, text: str, **kwargs):
            answer = TelegramLikeAnswer(text, self.chat_id, self.id)
            self.replies.append(answer)
            return answer

    class ParallelSearchGateway(FakeAgentGateway):
        async def run(self, request):
            yield AgentEvent(
                type="run_started",
                run_id=request.run_id,
                session_id="session-1",
            )
            for _ in range(2):
                yield AgentEvent(
                    type="tool_snapshot",
                    phase="completed",
                    tool="web_search",
                    summary="Web search completed",
                )
            yield AgentEvent(type="text_delta", delta="Final answer", reset=True)
            yield AgentEvent(
                type="run_completed",
                session_id="session-1",
                entry_id="entry-1",
                answer="Final answer",
            )

    responder = AIResponder(ParallelSearchGateway(), edit_cadence=0)
    trigger = TelegramLikeMessage("/ai search")
    request = AgentRunRequest(
        run_id="11111111-1111-4111-8111-111111111111",
        session_id=None,
        parent_entry_id=None,
        prompt="search",
        context=(),
        system_prompt=PromptBuilder().system_prompt,
        tool_policy="owner",
    )

    result = await responder.answer(trigger, request)

    assert result.succeeded is True
    assert trigger.replies[0].text == "Final answer"


@pytest.mark.asyncio
async def test_provider_rate_limit_gets_an_explicit_telegram_message():
    class RateLimitedGateway(FakeAgentGateway):
        async def run(self, request):
            yield AgentEvent(
                type="run_started",
                run_id=request.run_id,
                session_id="session-1",
            )
            yield AgentEvent(
                type="run_failed",
                code="RATE_LIMITED",
                message="Agent provider is temporarily rate limited",
            )

    responder = AIResponder(RateLimitedGateway(), edit_cadence=0)
    trigger = FakeMessage("/ai hello")
    request = AgentRunRequest(
        run_id="11111111-1111-4111-8111-111111111111",
        session_id=None,
        parent_entry_id=None,
        prompt="hello",
        context=(),
        system_prompt=PromptBuilder().system_prompt,
        tool_policy="owner",
    )

    result = await responder.answer(trigger, request)

    assert result.succeeded is False
    assert trigger.replies[0].text == (
        "AI provider is temporarily rate limited. Try again later."
    )


@pytest.mark.asyncio
async def test_handler_maps_answers_to_pi_sessions_and_forks_by_entry():
    gateway = FakeAgentGateway(["root answer", "child answer", "fork answer"])
    store = FakeStore(allowed={20})
    handler = AIConversationHandler(
        owner_id=10,
        responder=AIResponder(gateway, edit_cadence=0),
        store=store,
        prompt_builder=PromptBuilder(),
    )
    root = FakeMessage("/ai root prompt", sender_id=20)
    await handler.handle(root)
    root_answer = root.replies[0]
    root_marker = store.markers[(root.chat_id, root_answer.id)]

    child = FakeMessage("child prompt", sender_id=20, reply_to=root_answer)
    await handler.handle(child)
    fork = FakeMessage("fork prompt", sender_id=20, reply_to=root_answer)
    await handler.handle(fork)

    assert gateway.requests[0].tool_policy == "delegated"
    assert gateway.requests[0].session_id is None
    assert gateway.requests[1].session_id == root_marker.agent_session_id
    assert gateway.requests[1].parent_entry_id == root_marker.agent_entry_id
    assert gateway.requests[2].session_id == root_marker.agent_session_id
    assert gateway.requests[2].parent_entry_id == root_marker.agent_entry_id
    assert all(request.prompt != "child prompt" for request in [gateway.requests[2]])


@pytest.mark.asyncio
async def test_ai_cancel_aborts_only_the_requesters_active_run():
    gateway = BlockingAgentGateway()
    store = FakeStore()
    handler = AIConversationHandler(
        owner_id=10,
        responder=AIResponder(gateway, edit_cadence=0),
        store=store,
        prompt_builder=PromptBuilder(),
    )
    trigger = FakeMessage("/ai wait")
    running = asyncio.create_task(handler.handle(trigger))
    await gateway.started.wait()
    run_id = gateway.requests[0].run_id

    cancel = FakeMessage("/ai_cancel")
    assert await handler.handle(cancel) is True
    await running

    assert gateway.cancelled == [run_id]
    assert cancel.replies[0].text == "AI request cancellation requested."
    assert trigger.replies[0].text == "AI request cancelled."
