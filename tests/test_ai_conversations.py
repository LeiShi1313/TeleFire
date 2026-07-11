from collections.abc import AsyncIterator

import pytest

from telefire.ai import (
    AIAnswerMarker,
    AIConversationHandler,
    AIStateRepository,
    AIResponder,
    AISettings,
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


class FakeGateway:
    def __init__(self, answers: list[str]):
        self.answers = iter(answers)
        self.requests: list[list[dict[str, str]]] = []

    async def stream(self, messages: list[dict[str, str]]) -> AsyncIterator[str]:
        self.requests.append(messages)
        yield next(self.answers)


class FakeStore:
    def __init__(self):
        self.markers: dict[tuple[int, int], AIAnswerMarker] = {}

    async def get_answer(self, chat_id: int, answer_message_id: int):
        return self.markers.get((chat_id, answer_message_id))

    async def get_branch(self, chat_id: int, answer_message_id: int, limit: int):
        branch = []
        current_id = answer_message_id
        while current_id is not None and len(branch) < limit:
            marker = self.markers.get((chat_id, current_id))
            if marker is None:
                break
            branch.append(marker)
            current_id = marker.parent_answer_message_id
        return list(reversed(branch))

    async def save_answer(self, marker: AIAnswerMarker):
        self.markers[(marker.chat_id, marker.answer_message_id)] = marker

    async def is_allowed(self, user_id: int):
        return False

    async def get_last_request_at(self, user_id: int):
        return None

    async def set_last_request_at(self, user_id: int, timestamp: float):
        return None


def make_handler(gateway, store=None, **builder_options):
    store = store or FakeStore()
    return (
        AIConversationHandler(
            owner_id=10,
            responder=AIResponder(gateway, edit_cadence=0),
            store=store,
            prompt_builder=PromptBuilder(**builder_options),
        ),
        store,
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
    assert request[0] == {
        "role": "system",
        "content": AISettings.DEFAULT_SYSTEM_PROMPT,
    }
    assert request[-1] == {"role": "user", "content": "which database fits?"}
    assert request[1]["role"] == "user"
    assert "Untrusted reply context" in request[1]["content"]
    assert "We are comparing SQLite" in request[1]["content"]
    assert "/ai in quoted text" in request[1]["content"]
    marker = next(iter(store.markers.values()))
    assert marker.prompt == "which database fits?"
    assert marker.reference_context == request[1]["content"]


@pytest.mark.asyncio
async def test_direct_reply_to_ai_answer_continues_without_ai_command():
    gateway = FakeGateway(["First answer", "Follow-up answer"])
    handler, store = make_handler(gateway)
    trigger = FakeMessage("/ai explain vectors")
    await handler.handle(trigger)
    first_answer = trigger.replies[0]

    follow_up = FakeMessage("Give me an example", reply_to=first_answer)
    assert await handler.handle(follow_up) is True

    assert gateway.requests[1] == [
        {"role": "system", "content": AISettings.DEFAULT_SYSTEM_PROMPT},
        {"role": "user", "content": "explain vectors"},
        {"role": "assistant", "content": "First answer"},
        {"role": "user", "content": "Give me an example"},
    ]
    second_marker = store.markers[(follow_up.chat_id, follow_up.replies[0].id)]
    assert second_marker.parent_answer_message_id == first_answer.id


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

    fork_request = gateway.requests[2]
    assert {"role": "user", "content": "root question"} in fork_request
    assert {"role": "assistant", "content": "Root answer"} in fork_request
    assert {"role": "user", "content": "fork question"} in fork_request
    assert all("sibling" not in message["content"].lower() for message in fork_request)


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
        assert gateway.requests[0][-3:] == [
            {"role": "user", "content": "persisted question"},
            {"role": "assistant", "content": "persisted answer"},
            {"role": "user", "content": "after restart"},
        ]
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

    context = gateway.requests[0][1]["content"]
    assert "newest context" in context
    assert "middle context" in context
    assert "oldest context" not in context
    assert len(context) <= 160
