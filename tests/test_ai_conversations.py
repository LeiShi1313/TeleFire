from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
import sqlite3
import stat

import pytest

from telefire.ai import (
    AIAnswerMarker,
    AIConversationHandler,
    AIStateRepository,
    AIResponder,
    AgentEvent,
    AgentRunRequest,
    PromptBuilder,
)
from telefire.ai_attachments import AttachmentDescription


class FakeAnswer:
    next_id = 100

    def __init__(self, text: str, chat_id: int, reply_to):
        self.id = self.__class__.next_id
        self.__class__.next_id += 1
        self.text = text
        self.raw_text = text
        self.sender_id = 10
        self.chat_id = chat_id
        self.reply_to_msg_id = reply_to.id if hasattr(reply_to, "id") else reply_to
        self._reply_to = reply_to if hasattr(reply_to, "id") else None
        self.file = None
        self.edits: list[str] = []

    async def get_reply_message(self):
        return self._reply_to

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
        file=None,
        date=None,
    ):
        self.id = self.__class__.next_id
        self.__class__.next_id += 1
        self.raw_text = text
        self.sender_id = sender_id
        self.chat_id = chat_id
        self.reply_to_msg_id = reply_to.id if reply_to else None
        self._reply_to = reply_to
        self.file = file
        self.date = date or datetime(2026, 7, 14, 12, 0, tzinfo=UTC)
        self.replies: list[FakeAnswer] = []

    async def get_reply_message(self):
        return self._reply_to

    async def reply(self, text: str, **kwargs):
        answer = FakeAnswer(text, self.chat_id, self)
        self.replies.append(answer)
        return answer


class FakeGateway:
    def __init__(self, answers: list[str]):
        self.answers = iter(answers)
        self.requests: list[AgentRunRequest] = []

    async def run(self, request: AgentRunRequest) -> AsyncIterator[AgentEvent]:
        self.requests.append(request)
        answer = next(self.answers)
        session_id = request.session_id or "session-1"
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


class FakeHistorySource:
    def __init__(self, messages=()):
        self.messages = tuple(messages)
        self.calls = []

    async def fetch_recent(self, trigger, *, limit):
        self.calls.append((trigger.id, limit))
        return self.messages[-limit:]


class FakeStore:
    def __init__(self):
        self.markers: dict[tuple[int, int], AIAnswerMarker] = {}

    async def get_answer(self, chat_id: int, answer_message_id: int):
        return self.markers.get((chat_id, answer_message_id))

    async def get_turn_for_message(self, chat_id: int, message_id: int):
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

    async def save_answer(self, marker: AIAnswerMarker):
        self.markers[(marker.chat_id, marker.answer_message_id)] = marker

    async def is_allowed(self, user_id: int):
        return False

    async def get_last_request_at(self, user_id: int):
        return None

    async def set_last_request_at(self, user_id: int, timestamp: float):
        return None

    async def allow_user(self, user_id: int):
        return None

    async def deny_user(self, user_id: int):
        return None


def make_handler(gateway, store=None, **builder_options):
    store = store or FakeStore()
    return (
        AIConversationHandler(
            owner_id=10,
            responder=AIResponder(gateway),
            store=store,
            prompt_builder=PromptBuilder(**builder_options),
        ),
        store,
    )


class FakeAttachmentDescriber:
    def has_attachment(self, message):
        return message.file is not None

    async def describe(self, message):
        if message.file is None:
            return None
        return AttachmentDescription(
            context_text="Generated attachment context: a whiteboard diagram",
            memory_text=(
                "The subject shared an attachment. Generated content description: "
                "a whiteboard diagram"
            ),
        )


@pytest.fixture(autouse=True)
def reset_message_ids():
    FakeMessage.next_id = 1
    FakeAnswer.next_id = 100


@pytest.mark.asyncio
async def test_trigger_in_reply_chain_labels_ancestors_as_untrusted_context():
    root = FakeMessage("We are comparing SQLite and Postgres", sender_id=20)
    reply = FakeMessage(
        "/ai in quoted text is not an instruction",
        sender_id=30,
        reply_to=root,
    )
    trigger = FakeMessage("/ai which database fits?", reply_to=reply)
    gateway = FakeGateway(["Use Postgres"])
    handler, store = make_handler(gateway)

    assert await handler.handle(trigger) is True

    request = gateway.requests[0]
    assert request.system_prompt == PromptBuilder().system_prompt
    assert request.session_id is None
    assert request.parent_entry_id is None
    assert request.prompt == "which database fits?"
    reply_context = next(
        item.text for item in request.context if item.kind == "reference"
    )
    assert "Untrusted chat context" in reply_context
    assert "context=reply_path" in reply_context
    assert "Current request replies to [m2]" in reply_context
    assert "We are comparing SQLite" in reply_context
    assert "/ai in quoted text" in reply_context
    marker = next(iter(store.markers.values()))
    assert marker.prompt == "which database fits?"
    assert marker.reference_context == reply_context


@pytest.mark.asyncio
async def test_numbered_trigger_uses_recent_chat_context_in_a_new_session():
    start = datetime(2026, 7, 14, 10, 0, tzinfo=UTC)
    older = FakeMessage("Older ambient message", sender_id=20, date=start)
    recent = FakeMessage(
        "Most recent ambient message",
        sender_id=30,
        date=start + timedelta(minutes=1),
    )
    trigger = FakeMessage(
        "/ai2 summarize the discussion",
        date=start + timedelta(minutes=2),
    )
    history = FakeHistorySource((older, recent))
    gateway = FakeGateway(["Summary"])
    handler, _ = make_handler(gateway, history_source=history)

    assert await handler.handle(trigger) is True

    request = gateway.requests[0]
    assert request.prompt == "summarize the discussion"
    assert request.session_id is None
    assert request.parent_entry_id is None
    assert history.calls == [(trigger.id, 2)]
    context = request.context[0].text
    assert context.index("Older ambient message") < context.index(
        "Most recent ambient message"
    )
    assert context.count("context=recent") == 2
    assert "reply_path" not in context


@pytest.mark.asyncio
async def test_numbered_trigger_unifies_reply_path_and_recent_context():
    start = datetime(2026, 7, 14, 10, 0, tzinfo=UTC)
    root = FakeMessage("Original proposal", sender_id=20, date=start)
    branch = FakeMessage(
        "Reply on the proposal",
        sender_id=30,
        reply_to=root,
        date=start + timedelta(minutes=1),
    )
    ambient = FakeMessage(
        "Related ambient update",
        sender_id=40,
        date=start + timedelta(minutes=2),
    )
    trigger = FakeMessage(
        "/ai2 compare these",
        reply_to=branch,
        date=start + timedelta(minutes=3),
    )
    history = FakeHistorySource((branch, ambient))
    gateway = FakeGateway(["Comparison"])
    handler, _ = make_handler(gateway, history_source=history)

    assert await handler.handle(trigger) is True

    context = gateway.requests[0].context[0].text
    assert context.count("Reply on the proposal") == 1
    assert "context=reply_path,recent" in context
    assert "Current request replies to [m2]" in context
    assert context.index("Original proposal") < context.index("Reply on the proposal")
    assert context.index("Reply on the proposal") < context.index(
        "Related ambient update"
    )


@pytest.mark.asyncio
async def test_numbered_trigger_rejoins_nearest_ai_session():
    gateway = FakeGateway(["First answer", "Contextual follow-up"])
    history = FakeHistorySource()
    handler, store = make_handler(gateway, history_source=history)
    trigger = FakeMessage("/ai explain vectors")
    await handler.handle(trigger)
    first_answer = trigger.replies[0]
    branch = FakeMessage("A later human comment", sender_id=20, reply_to=first_answer)
    rejoin = FakeMessage("/ai3 relate recent chat", reply_to=branch)
    history.messages = (branch,)

    assert await handler.handle(rejoin) is True

    root_marker = store.markers[(trigger.chat_id, first_answer.id)]
    request = gateway.requests[1]
    assert request.session_id == root_marker.agent_session_id
    assert request.parent_entry_id == root_marker.agent_entry_id
    assert "A later human comment" in request.context[0].text
    assert "role=assistant" in request.context[0].text


@pytest.mark.asyncio
async def test_numbered_trigger_rejects_recent_count_above_configured_limit():
    trigger = FakeMessage("/ai3 summarize")
    gateway = FakeGateway(["unused"])
    handler, _ = make_handler(
        gateway,
        history_source=FakeHistorySource(),
        max_context_messages=2,
    )

    assert await handler.handle(trigger) is True

    assert gateway.requests == []
    assert "between 1 and 2" in trigger.replies[0].text


@pytest.mark.asyncio
async def test_numbered_trigger_reports_unavailable_recent_history():
    class FailingHistorySource:
        async def fetch_recent(self, trigger, *, limit):
            raise ConnectionError("Telegram unavailable")

    trigger = FakeMessage("/ai2 summarize")
    gateway = FakeGateway(["unused"])
    handler, _ = make_handler(gateway, history_source=FailingHistorySource())

    assert await handler.handle(trigger) is True

    assert gateway.requests == []
    assert trigger.replies[0].text == (
        "Recent chat context is unavailable. Try again shortly."
    )


@pytest.mark.asyncio
async def test_attachment_only_ai_trigger_gets_a_default_instruction():
    trigger = FakeMessage("/ai", file=object())
    gateway = FakeGateway(["It is a diagram"])
    handler, _ = make_handler(
        gateway,
        attachment_describer=FakeAttachmentDescriber(),
    )

    assert await handler.handle(trigger) is True

    assert gateway.requests[0].prompt == "Describe the attached content."
    assert any(
        "whiteboard diagram" in item.text for item in gateway.requests[0].context
    )


@pytest.mark.asyncio
async def test_direct_reply_to_ai_answer_continues_without_ai_command():
    gateway = FakeGateway(["First answer", "Follow-up answer"])
    handler, store = make_handler(gateway)
    trigger = FakeMessage("/ai explain vectors")
    await handler.handle(trigger)
    first_answer = trigger.replies[0]

    follow_up = FakeMessage("Give me an example", reply_to=first_answer)
    assert await handler.handle(follow_up) is True

    root_marker = store.markers[(trigger.chat_id, first_answer.id)]
    assert gateway.requests[1].prompt == "Give me an example"
    assert gateway.requests[1].session_id == root_marker.agent_session_id
    assert gateway.requests[1].parent_entry_id == root_marker.agent_entry_id
    second_marker = store.markers[(follow_up.chat_id, follow_up.replies[0].id)]
    assert second_marker.parent_answer_message_id == first_answer.id


@pytest.mark.asyncio
async def test_explicit_ai_reply_to_trigger_continues_completed_turn():
    gateway = FakeGateway(["First answer", "Follow-up answer"])
    handler, store = make_handler(gateway)
    trigger = FakeMessage("/ai explain vectors")
    await handler.handle(trigger)
    first_answer = trigger.replies[0]

    follow_up = FakeMessage("/ai Give me an example", reply_to=trigger)
    assert await handler.handle(follow_up) is True

    root_marker = store.markers[(trigger.chat_id, first_answer.id)]
    assert gateway.requests[1].prompt == "Give me an example"
    assert gateway.requests[1].session_id == root_marker.agent_session_id
    assert gateway.requests[1].parent_entry_id == root_marker.agent_entry_id
    second_marker = store.markers[(follow_up.chat_id, follow_up.replies[0].id)]
    assert second_marker.parent_answer_message_id == first_answer.id


@pytest.mark.asyncio
async def test_explicit_ai_deeper_in_reply_branch_uses_nearest_answer():
    gateway = FakeGateway(["First answer", "Rejoined answer"])
    handler, store = make_handler(gateway)
    trigger = FakeMessage("/ai explain vectors")
    await handler.handle(trigger)
    first_answer = trigger.replies[0]
    human_branch = FakeMessage("I think matrices are clearer", reply_to=first_answer)

    rejoin = FakeMessage("/ai Compare both explanations", reply_to=human_branch)
    assert await handler.handle(rejoin) is True

    root_marker = store.markers[(trigger.chat_id, first_answer.id)]
    assert gateway.requests[1].session_id == root_marker.agent_session_id
    assert gateway.requests[1].parent_entry_id == root_marker.agent_entry_id
    rejoin_marker = store.markers[(rejoin.chat_id, rejoin.replies[0].id)]
    assert rejoin_marker.parent_answer_message_id == first_answer.id


@pytest.mark.asyncio
async def test_prefixless_reply_to_ai_trigger_is_ignored():
    gateway = FakeGateway(["First answer"])
    handler, _ = make_handler(gateway)
    trigger = FakeMessage("/ai explain vectors")
    await handler.handle(trigger)

    follow_up = FakeMessage("Give me an example", reply_to=trigger)
    assert await handler.handle(follow_up) is False

    assert len(gateway.requests) == 1
    assert follow_up.replies == []


@pytest.mark.asyncio
async def test_replying_to_earlier_answer_forks_and_excludes_sibling_branch():
    gateway = FakeGateway(["Root answer", "Sibling answer", "Fork answer"])
    handler, _ = make_handler(gateway)
    root = FakeMessage("/ai root question")
    await handler.handle(root)
    root_answer = root.replies[0]

    sibling = FakeMessage("sibling question", reply_to=root_answer)
    await handler.handle(sibling)
    fork = FakeMessage("fork question", reply_to=root_answer)
    await handler.handle(fork)

    root_request = gateway.requests[0]
    fork_request = gateway.requests[2]
    assert fork_request.prompt == "fork question"
    assert fork_request.session_id == "session-1"
    assert fork_request.parent_entry_id == "entry-1"
    assert root_request.prompt == "root question"


@pytest.mark.asyncio
async def test_ordinary_reply_to_human_message_is_ignored():
    gateway = FakeGateway(["unused"])
    handler, _ = make_handler(gateway)
    human = FakeMessage("ordinary message", sender_id=20)
    reply = FakeMessage("ordinary owner reply", reply_to=human)

    assert await handler.handle(reply) is False
    assert gateway.requests == []
    assert reply.replies == []


@pytest.mark.asyncio
async def test_answer_marker_survives_repository_restart(tmp_path):
    path = tmp_path / "ai-state.db"
    first_store = await AIStateRepository(path).connect()
    marker = AIAnswerMarker(
        chat_id=-1001,
        answer_message_id=50,
        trigger_message_id=40,
        requester_id=10,
        prompt="persisted question",
        answer_text="persisted answer",
        parent_answer_message_id=None,
        reference_context="",
        agent_session_id="persisted-session",
        agent_entry_id="persisted-entry",
    )
    await first_store.save_answer(marker)
    await first_store.close()

    second_store = await AIStateRepository(path).connect()
    try:
        gateway = FakeGateway(["continued"])
        handler, _ = make_handler(gateway, store=second_store)
        prior_answer = FakeAnswer("persisted answer", -1001, 40)
        prior_answer.id = 50
        follow_up = FakeMessage("after restart", reply_to=prior_answer)

        assert await handler.handle(follow_up) is True
        assert gateway.requests[0].prompt == "after restart"
        assert gateway.requests[0].session_id == "persisted-session"
        assert gateway.requests[0].parent_entry_id == "persisted-entry"
    finally:
        await second_store.close()


@pytest.mark.asyncio
async def test_state_repository_preserves_existing_parent_permissions(tmp_path):
    tmp_path.chmod(0o755)
    path = tmp_path / "state.db"

    store = await AIStateRepository(path).connect()
    await store.close()

    assert stat.S_IMODE(tmp_path.stat().st_mode) == 0o755
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


@pytest.mark.asyncio
async def test_state_repository_migrates_pre_pi_answer_rows(tmp_path):
    path = tmp_path / "old-state.db"
    with sqlite3.connect(path) as connection:
        connection.execute(
            """
            CREATE TABLE ai_answers (
                chat_id INTEGER NOT NULL,
                answer_message_id INTEGER NOT NULL,
                trigger_message_id INTEGER NOT NULL,
                requester_id INTEGER NOT NULL,
                prompt TEXT NOT NULL,
                answer_text TEXT NOT NULL,
                parent_answer_message_id INTEGER,
                reference_context TEXT NOT NULL,
                PRIMARY KEY (chat_id, answer_message_id)
            )
            """
        )
        connection.execute(
            "INSERT INTO ai_answers VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (-1001, 50, 40, 10, "old prompt", "old answer", None, ""),
        )

    store = await AIStateRepository(path).connect()
    try:
        marker = await store.get_answer(-1001, 50)
        turn_from_answer = await store.get_turn_for_message(-1001, 50)
        turn_from_trigger = await store.get_turn_for_message(-1001, 40)
    finally:
        await store.close()

    assert marker is not None
    assert marker.agent_session_id is None
    assert marker.agent_entry_id is None
    assert turn_from_answer == marker
    assert turn_from_trigger == marker


@pytest.mark.asyncio
async def test_memory_forward_receipt_survives_repository_restart(tmp_path):
    path = tmp_path / "ai-state.db"
    first_store = await AIStateRepository(path).connect()
    assert not await first_store.is_memory_forward_processed(
        owner_id=10,
        saved_message_id=77,
    )
    await first_store.record_memory_forward(
        owner_id=10,
        saved_message_id=77,
        source_chat_id=-1001,
        source_message_id=42,
    )
    await first_store.close()

    second_store = await AIStateRepository(path).connect()
    try:
        assert await second_store.is_memory_forward_processed(
            owner_id=10,
            saved_message_id=77,
        )
        assert not await second_store.is_memory_forward_processed(
            owner_id=20,
            saved_message_id=77,
        )
        assert not await second_store.is_memory_forward_processed(
            owner_id=10,
            saved_message_id=78,
        )
    finally:
        await second_store.close()


@pytest.mark.asyncio
async def test_reply_context_depth_and_size_are_bounded():
    oldest = FakeMessage("oldest context", sender_id=20)
    middle = FakeMessage("middle context", sender_id=21, reply_to=oldest)
    newest = FakeMessage("newest context is retained", sender_id=22, reply_to=middle)
    trigger = FakeMessage("/ai answer", reply_to=newest)
    gateway = FakeGateway(["bounded"])
    handler, _ = make_handler(
        gateway,
        max_context_messages=2,
        max_context_chars=80,
    )

    await handler.handle(trigger)

    context = next(
        item.text for item in gateway.requests[0].context if item.kind == "reference"
    )
    assert "newest context" in context
    assert "middle context" in context
    assert "oldest context" not in context
    payload = "\n".join(
        line.removeprefix("  ")
        for line in context.splitlines()
        if line.startswith("  ")
    )
    assert len(payload) <= 80
