from __future__ import annotations

import os
from datetime import UTC, datetime
from uuid import uuid4

import aiohttp
import pytest

from telefire.ai_memory import HindsightMemoryClient, MemoryEpisode, MemoryEvent


HINDSIGHT_URL = os.environ.get("TELEFIRE_HINDSIGHT_URL", "").rstrip("/")

pytestmark = pytest.mark.skipif(
    not HINDSIGHT_URL,
    reason="TELEFIRE_HINDSIGHT_URL is required for the real Hindsight contract",
)


async def _request(
    session: aiohttp.ClientSession,
    method: str,
    path: str,
    *,
    json: dict | None = None,
) -> dict:
    async with session.request(
        method,
        f"{HINDSIGHT_URL}{path}",
        json=json,
    ) as response:
        payload = await response.json()
        assert response.status < 300, payload
        assert isinstance(payload, dict)
        return payload


async def _delete_bank(session: aiohttp.ClientSession, bank_id: str) -> None:
    async with session.delete(f"{HINDSIGHT_URL}/v1/default/banks/{bank_id}"):
        pass


@pytest.mark.asyncio
async def test_hindsight_accepts_the_production_episode_serializer():
    suffix = uuid4().hex[:10]
    bank_id = f"contract-episode-{suffix}"
    document_id = f"telegram:thread:-100901:{suffix}"
    preference = f"serializer-preference-{suffix}"
    episode = MemoryEpisode(
        scope_id=bank_id,
        scope_display_name="Serializer Contract Group",
        document_id=document_id,
        source="telegram",
        events=(
            MemoryEvent(
                source_id=f"telegram:message:-100901:{suffix}",
                actor_id="telegram:user:49001",
                actor_display_name="Alice Serializer",
                occurred_at=datetime(2026, 7, 13, 8, 0, tzinfo=UTC),
                mentioned_at=datetime(2026, 7, 13, 8, 0, tzinfo=UTC),
                text=f"In this chat I am called Quill, and I prefer {preference}.",
            ),
        ),
    )
    client = HindsightMemoryClient(HINDSIGHT_URL, timeout=300)
    async with aiohttp.ClientSession() as session:
        try:
            retained = await client.retain(episode)
            assert retained.accepted is True
            assert retained.items_count == 1
            assert (
                await client.get_document_content(
                    scope_id=bank_id,
                    document_id=document_id,
                )
                == episode.content
            )
            recalled = await client.recall(
                scope_id=bank_id,
                query="What does the person called Quill prefer?",
            )
            assert any(preference in memory.text for memory in recalled.memories)
            assert any(
                "telegram:user:49001" in memory.entities for memory in recalled.memories
            )
        finally:
            await client.close()
            await _delete_bank(session, bank_id)


@pytest.mark.asyncio
async def test_hindsight_retain_recall_isolation_append_and_invalidation():
    suffix = uuid4().hex[:10]
    bank_id = f"contract-primary-{suffix}"
    other_bank_id = f"contract-isolated-{suffix}"
    document_id = "telegram-thread-701"
    original = (
        "Alice Example <telegram:user:42001> (2026-07-13T09:00:00Z): "
        f"Project Helios-{suffix} launches on Friday.\n"
        "Bob Example <telegram:user:42002> (2026-07-13T09:02:00Z, replying to Alice): "
        "I will complete the security review before launch."
    )
    item = {
        "content": original,
        "context": "Telegram group Engineering",
        "timestamp": "2026-07-13T09:02:00Z",
        "document_id": document_id,
        "update_mode": "replace",
        "metadata": {
            "client": "telefire-contract",
            "scope_id": "telegram:chat:-100701",
        },
        "entities": [
            {"text": "Alice Example <telegram:user:42001>", "type": "PERSON"},
            {"text": "Bob Example <telegram:user:42002>", "type": "PERSON"},
        ],
    }

    async with aiohttp.ClientSession(
        timeout=aiohttp.ClientTimeout(total=300)
    ) as session:
        try:
            first = await _request(
                session,
                "POST",
                f"/v1/default/banks/{bank_id}/memories",
                json={"items": [item], "async": False},
            )
            assert first["success"] is True
            assert first["bank_id"] == bank_id
            assert first["items_count"] == 1

            retry = await _request(
                session,
                "POST",
                f"/v1/default/banks/{bank_id}/memories",
                json={"items": [item], "async": False},
            )
            assert retry["success"] is True

            documents = await _request(
                session,
                "GET",
                f"/v1/default/banks/{bank_id}/documents",
            )
            assert documents["total"] == 1
            assert documents["items"][0]["id"] == document_id

            recalled = await _request(
                session,
                "POST",
                f"/v1/default/banks/{bank_id}/memories/recall",
                json={
                    "query": f"When does Project Helios-{suffix} launch and who owns security review?",
                    "budget": "mid",
                    "max_tokens": 2000,
                    "trace": True,
                    "include": {
                        "entities": {"max_tokens": 500},
                        "chunks": {"max_tokens": 1000},
                        "source_facts": {"max_tokens": 1000},
                    },
                },
            )
            assert recalled["results"]
            assert any(
                f"Helios-{suffix}" in result["text"] for result in recalled["results"]
            )
            assert recalled["chunks"]
            assert recalled["entities"]
            assert recalled["trace"]

            isolated = await _request(
                session,
                "POST",
                f"/v1/default/banks/{other_bank_id}/memories/recall",
                json={
                    "query": f"Project Helios-{suffix}",
                    "budget": "low",
                    "max_tokens": 500,
                },
            )
            assert not any(
                f"Helios-{suffix}" in result["text"] for result in isolated["results"]
            )

            appended = await _request(
                session,
                "POST",
                f"/v1/default/banks/{bank_id}/memories",
                json={
                    "items": [
                        {
                            "content": (
                                "Alice Example <telegram:user:42001> "
                                "(2026-07-13T10:00:00Z): The launch moved to Wednesday."
                            ),
                            "context": "Telegram group Engineering",
                            "timestamp": "2026-07-13T10:00:00Z",
                            "document_id": document_id,
                            "update_mode": "append",
                            "entities": item["entities"],
                        }
                    ],
                    "async": False,
                },
            )
            assert appended["success"] is True

            document = await _request(
                session,
                "GET",
                f"/v1/default/banks/{bank_id}/documents/{document_id}",
            )
            assert original in document["original_text"]
            assert "moved to Wednesday" in document["original_text"]

            replaced_content = (
                original.replace(
                    "I will complete the security review before launch.",
                    "I completed the security review before launch.",
                )
                + "\nAlice Example <telegram:user:42001> "
                "(2026-07-13T10:00:00Z): The launch moved to Wednesday."
            )
            replaced = await _request(
                session,
                "POST",
                f"/v1/default/banks/{bank_id}/memories",
                json={
                    "items": [
                        {
                            **item,
                            "content": replaced_content,
                            "timestamp": "2026-07-13T10:00:00Z",
                            "update_mode": "replace",
                        }
                    ],
                    "async": False,
                },
            )
            assert replaced["success"] is True
            replaced_document = await _request(
                session,
                "GET",
                f"/v1/default/banks/{bank_id}/documents/{document_id}",
            )
            assert replaced_document["original_text"] == replaced_content
            assert "will complete" not in replaced_document["original_text"]

            current = await _request(
                session,
                "POST",
                f"/v1/default/banks/{bank_id}/memories/recall",
                json={
                    "query": f"What is the latest launch day for Helios-{suffix}?",
                    "budget": "mid",
                    "max_tokens": 1000,
                },
            )
            memory = next(
                result for result in current["results"] if "Wednesday" in result["text"]
            )
            invalidated = await _request(
                session,
                "PATCH",
                f"/v1/default/banks/{bank_id}/memories/{memory['id']}",
                json={
                    "state": "invalidated",
                    "reason": "contract test reversible suppression",
                },
            )
            assert invalidated["state"] == "invalidated"

            after = await _request(
                session,
                "POST",
                f"/v1/default/banks/{bank_id}/memories/recall",
                json={
                    "query": f"What is the latest launch day for Helios-{suffix}?",
                    "budget": "mid",
                    "max_tokens": 1000,
                },
            )
            assert memory["id"] not in {result["id"] for result in after["results"]}

            restored = await _request(
                session,
                "PATCH",
                f"/v1/default/banks/{bank_id}/memories/{memory['id']}",
                json={"state": "valid", "reason": "contract test cleanup"},
            )
            assert restored["state"] == "valid"
        finally:
            await _delete_bank(session, bank_id)
            await _delete_bank(session, other_bank_id)


@pytest.mark.asyncio
async def test_hindsight_stable_actor_entity_survives_display_name_change():
    suffix = uuid4().hex[:10]
    bank_id = f"contract-identity-{suffix}"
    actor_id = f"telegram:user:44{suffix[:4]}"
    other_actor_id = f"telegram:user:55{suffix[:4]}"
    async with aiohttp.ClientSession(
        timeout=aiohttp.ClientTimeout(total=300)
    ) as session:
        try:
            for document_id, display_name, preference in (
                ("identity-1", "Alice Example", "tea"),
                ("identity-2", "Alicia Example", "coffee"),
            ):
                retained = await _request(
                    session,
                    "POST",
                    f"/v1/default/banks/{bank_id}/memories",
                    json={
                        "items": [
                            {
                                "content": (
                                    f"Actor {actor_id} [display_name: {display_name}] says: "
                                    f"I now prefer {preference}."
                                ),
                                "document_id": document_id,
                                "entities": [{"text": actor_id, "type": "PERSON"}],
                            }
                        ],
                        "async": False,
                    },
                )
                assert retained["success"] is True

            same_name = await _request(
                session,
                "POST",
                f"/v1/default/banks/{bank_id}/memories",
                json={
                    "items": [
                        {
                            "content": (
                                f"Actor {other_actor_id} [display_name: Alicia Example] says: "
                                "I prefer cycling."
                            ),
                            "document_id": "identity-3",
                            "entities": [{"text": other_actor_id, "type": "PERSON"}],
                        }
                    ],
                    "async": False,
                },
            )
            assert same_name["success"] is True

            entities = await _request(
                session,
                "GET",
                f"/v1/default/banks/{bank_id}/entities?limit=100",
            )
            stable = [
                entity
                for entity in entities["items"]
                if entity["canonical_name"] == actor_id
            ]
            assert len(stable) == 1
            assert stable[0]["mention_count"] == 2
            assert any(
                entity["canonical_name"] == other_actor_id
                and entity["mention_count"] == 1
                for entity in entities["items"]
            )

            recalled = await _request(
                session,
                "POST",
                f"/v1/default/banks/{bank_id}/memories/recall",
                json={
                    "query": f"What does {actor_id} prefer now?",
                    "budget": "mid",
                    "max_tokens": 1000,
                    "include": {"entities": {"max_tokens": 500}},
                },
            )
            assert actor_id in recalled["entities"]
            coffee = next(
                result
                for result in recalled["results"]
                if "coffee" in result["text"].lower()
            )
            assert actor_id in coffee["entities"]
            assert other_actor_id not in coffee["entities"]

            other = await _request(
                session,
                "POST",
                f"/v1/default/banks/{bank_id}/memories/recall",
                json={
                    "query": f"What does {other_actor_id} prefer?",
                    "budget": "mid",
                    "max_tokens": 1000,
                    "include": {"entities": {"max_tokens": 500}},
                },
            )
            cycling = next(
                result
                for result in other["results"]
                if "cycling" in result["text"].lower()
            )
            assert other_actor_id in cycling["entities"]
            assert actor_id not in cycling["entities"]
        finally:
            await _delete_bank(session, bank_id)


@pytest.mark.asyncio
async def test_hindsight_bounded_reflect_returns_cited_relationship_answer():
    suffix = uuid4().hex[:10]
    bank_id = f"contract-reflect-{suffix}"
    content = (
        "Mina Example <telegram:user:43001> (2026-07-13T08:00:00Z): "
        "My brother is Leon Example.\n"
        "Leon Example <telegram:user:43002> (2026-07-13T08:10:00Z): "
        "My knee still hurts, so I need a flat walking route.\n"
        "Omar Example <telegram:user:43003> (2026-07-13T08:20:00Z): "
        f"The Lakeside-{suffix} route is flat."
    )
    async with aiohttp.ClientSession(
        timeout=aiohttp.ClientTimeout(total=300)
    ) as session:
        try:
            retained = await _request(
                session,
                "POST",
                f"/v1/default/banks/{bank_id}/memories",
                json={
                    "items": [
                        {
                            "content": content,
                            "context": "Telegram group Weekend Walks",
                            "timestamp": "2026-07-13T08:20:00Z",
                            "document_id": "telegram-thread-801",
                            "entities": [
                                {
                                    "text": "Mina Example <telegram:user:43001>",
                                    "type": "PERSON",
                                },
                                {
                                    "text": "Leon Example <telegram:user:43002>",
                                    "type": "PERSON",
                                },
                                {
                                    "text": "Omar Example <telegram:user:43003>",
                                    "type": "PERSON",
                                },
                                {"text": f"Lakeside-{suffix}", "type": "PLACE"},
                            ],
                        }
                    ],
                    "async": False,
                },
            )
            assert retained["success"] is True

            reflected = await _request(
                session,
                "POST",
                f"/v1/default/banks/{bank_id}/reflect",
                json={
                    "query": (
                        "Which route suits Mina's brother? State the relationship, "
                        "constraint, and recommendation from memory."
                    ),
                    "budget": "mid",
                    "max_tokens": 1200,
                    "include": {
                        "facts": {"max_tokens": 1000},
                        "tool_calls": {"max_tokens": 1000},
                    },
                },
            )
            assert "Leon" in reflected["text"]
            assert f"Lakeside-{suffix}" in reflected["text"]
            assert "flat" in reflected["text"].lower()
            assert reflected["based_on"]["memories"]
            assert reflected["trace"]["tool_calls"]
        finally:
            await _delete_bank(session, bank_id)
