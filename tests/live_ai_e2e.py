from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from io import BytesIO
import os
from types import SimpleNamespace
from urllib.parse import quote
from uuid import uuid4

import aiohttp
from PIL import Image, ImageDraw
from telethon import TelegramClient, events, types, utils

from telefire.ai_memory import HindsightMemoryClient
from telefire.config import apply_config
from telefire.plugins.ai import TelegramAI
from telefire.telegram.config import TelegramRuntimeConfig


async def connect_account(account: str) -> TelegramClient:
    config = TelegramRuntimeConfig.from_account(account=account)
    client = TelegramClient(
        str(config.store_dir / config.session_name),
        config.api_id,
        config.api_hash,
    )
    await client.connect()
    if not await client.is_user_authorized():
        raise RuntimeError(f"Telegram E2E account {account!r} is not authorized")
    return client


async def start_owner_userbot(account: str) -> TelegramAI:
    userbot = TelegramAI(account=account, log_level="info")
    await userbot.service.connect()
    await userbot._setup()
    userbot.client.remove_event_handler(userbot._on_message)

    async def incoming_only(event) -> None:
        if event.message.sender_id != userbot._owner_id:
            await userbot._on_message(event)

    userbot._e2e_incoming_handler = incoming_only
    userbot.client.add_event_handler(incoming_only, events.NewMessage())
    return userbot


async def close_owner_userbot(userbot: TelegramAI) -> None:
    if userbot._continuous_memory_scheduler is not None:
        await userbot._continuous_memory_scheduler.close()
    if userbot._dream_scheduler is not None:
        await userbot._dream_scheduler.close()
    if userbot._memory is not None:
        await userbot._memory.close()
    await userbot._gateway.close()
    await userbot._store.close()
    await userbot.service.close()


async def owner_request(userbot: TelegramAI, entity, message, **kwargs):
    sent = await userbot.client.send_message(entity, message, **kwargs)
    await userbot._on_message(SimpleNamespace(message=sent))
    return sent


async def wait_for_health(url: str, timeout: float = 30) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    async with aiohttp.ClientSession() as session:
        while asyncio.get_running_loop().time() < deadline:
            try:
                async with session.get(f"{url.rstrip('/')}/health") as response:
                    if response.status == 200:
                        return
            except aiohttp.ClientError:
                pass
            await asyncio.sleep(0.25)
    raise RuntimeError("Hindsight did not become healthy")


async def direct_replies(client, chat, message_id: int, sender_id: int):
    replies = []
    async for message in client.iter_messages(chat, limit=120):
        if message.id <= message_id:
            break
        if message.reply_to_msg_id == message_id and message.sender_id == sender_id:
            replies.append(message)
    return replies


async def wait_for_reply(
    client,
    chat,
    trigger,
    sender_id: int,
    *,
    expected_text: str | tuple[str, ...] | None = None,
    timeout: float = 240,
):
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        replies = await direct_replies(client, chat, trigger.id, sender_id)
        if len(replies) > 1:
            raise RuntimeError("AI created more than one direct reply")
        if replies:
            reply = replies[0]
            text = (reply.raw_text or "").strip()
            if text in {"Thinking...", "Remembering..."}:
                await asyncio.sleep(0.5)
                continue
            if text.startswith(("AI request failed", "Memory update failed")):
                raise RuntimeError(f"Live E2E operation failed: {text}")
            if text.startswith("AI rate limit active"):
                raise RuntimeError("Live E2E request unexpectedly hit the rate limit")
            expected = (
                (expected_text,)
                if isinstance(expected_text, str)
                else (expected_text or ())
            )
            if any(value.lower() not in text.lower() for value in expected):
                await asyncio.sleep(0.5)
                continue
            return reply
        await asyncio.sleep(0.5)
    raise RuntimeError("Timed out waiting for the expected Telegram reply")


async def assert_no_reply(client, chat, trigger, sender_id: int, timeout: float = 5):
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if await direct_replies(client, chat, trigger.id, sender_id):
            raise RuntimeError("A request expected to be silent received a reply")
        await asyncio.sleep(0.25)


async def wait_for_document(
    hindsight_url: str,
    scope_id: str,
    document_id: str,
    expected: tuple[str, ...],
    *,
    timeout: float = 180,
) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    bank = quote(scope_id, safe="")
    document = quote(document_id, safe="")
    async with aiohttp.ClientSession() as session:
        while asyncio.get_running_loop().time() < deadline:
            async with session.get(
                f"{hindsight_url}/v1/default/banks/{bank}/documents/{document}"
            ) as response:
                if response.status == 200:
                    payload = await response.json()
                    source = payload.get("original_text", "")
                    if all(value in source for value in expected):
                        return
            await asyncio.sleep(1)
    raise RuntimeError(f"Timed out waiting for source document: {document_id}")


def exact_user_mention(user_id: int) -> tuple[str, list[types.TypeMessageEntity]]:
    label = "the explicitly mentioned test user"
    text = f"/ai What synthetic preference did {label} state?"
    offset = text.index(label)
    return text, [types.MessageEntityMentionName(offset, len(label), user_id)]


async def run() -> None:
    if os.environ.get("TELEFIRE_RUN_E2E") != "1":
        raise RuntimeError("Set TELEFIRE_RUN_E2E=1 to run the mutating live test")
    chat_raw = os.environ.get("TELEFIRE_E2E_CHAT_ID", "")
    if not chat_raw.startswith("-100"):
        raise RuntimeError(
            "TELEFIRE_E2E_CHAT_ID must be an explicit numeric megagroup ID"
        )
    chat_id = int(chat_raw)
    owner_account = os.environ.get(
        "TELEFIRE_E2E_OWNER_SESSION",
        os.environ.get("TELEFIRE_E2E_CONTROLLER_SESSION", "ai_e2e_peer"),
    )
    peer_account = os.environ.get("TELEFIRE_E2E_PEER_SESSION", "ai_e2e_peer2")
    hindsight_url = os.environ.get("TELEFIRE_HINDSIGHT_URL") or (
        "http://127.0.0.1:" + os.environ.get("TELEFIRE_HINDSIGHT_EXPOSE_PORT", "18888")
    )
    hindsight_url = hindsight_url.rstrip("/")
    os.environ.setdefault("TELEFIRE_HINDSIGHT_URL", hindsight_url)
    os.environ.setdefault(
        "TELEFIRE_PI_URL",
        "http://127.0.0.1:" + os.environ.get("TELEFIRE_PI_EXPOSE_PORT", "18790"),
    )
    await wait_for_health(hindsight_url)

    owner_userbot = await start_owner_userbot(owner_account)
    owner = owner_userbot.client
    peer = await connect_account(peer_account)
    memory = HindsightMemoryClient(hindsight_url, timeout=90)
    baseline_id = 0
    saved_baseline_id = 0
    chat = None
    scope_id = f"telegram:chat:{chat_id}"
    try:
        owner_identity, peer_identity = await asyncio.gather(
            owner.get_me(),
            peer.get_me(),
        )
        if owner_identity.id == peer_identity.id:
            raise RuntimeError("Live E2E sessions must be distinct Telegram identities")

        chat = await owner.get_entity(chat_id)
        peer_chat = await peer.get_entity(chat_id)
        if (
            utils.get_peer_id(chat) != chat_id
            or utils.get_peer_id(peer_chat) != chat_id
        ):
            raise RuntimeError("Both sessions must resolve the exact E2E chat")
        participants = [
            participant async for participant in owner.iter_participants(chat)
        ]
        if {participant.id for participant in participants} != {
            owner_identity.id,
            peer_identity.id,
        }:
            raise RuntimeError("E2E group must contain exactly the owner and peer")

        latest = await owner.get_messages(chat, limit=1)
        baseline_id = latest[0].id if latest else 0
        saved_latest = await owner.get_messages("me", limit=1)
        saved_baseline_id = saved_latest[0].id if saved_latest else 0
        token = uuid4().hex[:10]

        unauthorized = await peer.send_message(
            peer_chat,
            f"/ai reply with UNAUTHORIZED_{token}",
        )
        await assert_no_reply(owner, chat, unauthorized, owner_identity.id)
        print("unauthorized_silence=ok")

        access_target = await peer.send_message(peer_chat, f"access-target-{token}")
        allow = await owner_request(
            owner_userbot,
            chat,
            "/ai_allow",
            reply_to=access_target.id,
        )
        await wait_for_reply(
            owner, chat, allow, owner_identity.id, expected_text="AI access allowed."
        )
        print("whitelist_allow=ok")

        enable = await owner_request(owner_userbot, chat, "/ai_memory_enable")
        await wait_for_reply(
            owner,
            chat,
            enable,
            owner_identity.id,
            expected_text="Continuous memory enabled",
        )
        print("scope_enable=ok")

        standalone_token = f"DREAM_STANDALONE_{token}"
        thread_root_token = f"DREAM_ROOT_{token}"
        thread_reply_token = f"DREAM_REPLY_{token}"
        standalone = await peer.send_message(
            peer_chat,
            f"Standalone synthetic lore: {standalone_token}",
        )
        thread_root = await peer.send_message(
            peer_chat, f"Synthetic thread root: {thread_root_token}"
        )
        await owner.send_message(
            chat,
            f"Synthetic thread reply: {thread_reply_token}",
            reply_to=thread_root.id,
        )
        settlement = float(
            os.environ.get("TELEFIRE_MEMORY_DREAM_SETTLEMENT_SECONDS", "30")
        )
        await asyncio.sleep(max(settlement, 0) + 1)
        started_at = standalone.date.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")
        await wait_for_document(
            hindsight_url,
            scope_id,
            f"telegram:dream-session:{chat_id}:{started_at}:{standalone.id}",
            (standalone_token, thread_root_token, thread_reply_token),
        )
        print("standalone_and_thread_continuous_memory=ok")

        alias_token = f"ALIAS_{token}"
        preference_token = f"PREFERENCE_{token}"
        evidence = await peer.send_message(
            peer_chat,
            f"In this test chat I am called {alias_token}, and I prefer {preference_token}.",
        )
        remember = await owner_request(
            owner_userbot,
            chat,
            "/ai_memory",
            reply_to=evidence.id,
        )
        await wait_for_reply(
            owner,
            chat,
            remember,
            owner_identity.id,
            expected_text="Memory stored from reply chain",
        )
        print("explicit_memory=ok")

        mention_text, entities = exact_user_mention(peer_identity.id)
        explicit_question = await owner_request(
            owner_userbot,
            chat,
            mention_text,
            formatting_entities=entities,
        )
        explicit_answer = await wait_for_reply(
            owner,
            chat,
            explicit_question,
            owner_identity.id,
            expected_text=preference_token,
        )
        print("exact_mention_recall=ok")

        continuation = await owner_request(
            owner_userbot,
            chat,
            "Which scoped alias was connected to that preference?",
            reply_to=explicit_answer.id,
        )
        await wait_for_reply(
            owner,
            chat,
            continuation,
            owner_identity.id,
            expected_text=alias_token,
        )
        print("continuation=ok")

        implicit_question = await owner_request(
            owner_userbot,
            chat,
            f"/ai What synthetic preference does {alias_token} have in this chat?",
        )
        await wait_for_reply(
            owner,
            chat,
            implicit_question,
            owner_identity.id,
            expected_text=preference_token,
        )
        print("implicit_alias_recall=ok")

        delegated_token = f"DELEGATED_{token}"
        delegated = await peer.send_message(
            peer_chat,
            f"/ai Use code_exec to calculate 37 * 41, then include {delegated_token}.",
        )
        await wait_for_reply(
            owner,
            chat,
            delegated,
            owner_identity.id,
            expected_text=("1517", delegated_token),
        )
        print("delegated_code_exec=ok")

        image_token = f"ATTACHMENT_{token}"
        image_file = BytesIO()
        image_file.name = f"telefire-{token}.png"
        image = Image.new("RGB", (640, 320), "white")
        drawing = ImageDraw.Draw(image)
        drawing.rectangle((30, 30, 280, 290), fill="red")
        drawing.text((320, 140), image_token, fill="black")
        image.save(image_file, format="PNG")
        image_file.seek(0)
        attachment = await peer.send_file(
            peer_chat,
            image_file,
            caption=f"Synthetic group lore image {image_token}",
        )
        attachment_question = await owner_request(
            owner_userbot,
            chat,
            "/ai Describe the replied image, including its dominant color and visible token.",
            reply_to=attachment.id,
        )
        await wait_for_reply(
            owner,
            chat,
            attachment_question,
            owner_identity.id,
            expected_text=(image_token, "red"),
        )
        print("attachment_description=ok")

        link_root_token = f"SAVED_LINK_ROOT_{token}"
        link_reply_token = f"SAVED_LINK_REPLY_{token}"
        link_root = await peer.send_message(
            peer_chat, f"Saved-link synthetic root: {link_root_token}"
        )
        link_reply = await peer.send_message(
            peer_chat,
            f"Saved-link synthetic reply: {link_reply_token}",
            reply_to=link_root.id,
        )
        internal_chat_id = str(chat_id)[4:]
        saved_link = await owner_request(
            owner_userbot, "me", f"https://t.me/c/{internal_chat_id}/{link_reply.id}"
        )
        await wait_for_reply(
            owner,
            "me",
            saved_link,
            owner_identity.id,
            expected_text="Remembered.",
        )
        await wait_for_document(
            hindsight_url,
            scope_id,
            f"telegram:thread:{chat_id}:{link_root.id}",
            (link_root_token, link_reply_token),
        )
        print("saved_messages_source_link=ok")

        old_value = f"OLD_PLAN_{token}"
        new_value = f"NEW_PLAN_{token}"
        old_evidence = await peer.send_message(
            peer_chat, f"My current synthetic plan is {old_value}."
        )
        old_memory = await owner_request(
            owner_userbot,
            chat,
            "/ai_memory",
            reply_to=old_evidence.id,
        )
        await wait_for_reply(
            owner,
            chat,
            old_memory,
            owner_identity.id,
            expected_text="Memory stored from reply chain",
        )
        new_evidence = await peer.send_message(
            peer_chat, f"Correction: my current synthetic plan is {new_value}."
        )
        revision = await owner_request(
            owner_userbot,
            chat,
            f"/ai_memory Supersede {old_value}; the current plan is {new_value}.",
            reply_to=new_evidence.id,
        )
        await wait_for_reply(
            owner, chat, revision, owner_identity.id, expected_text="Memory updated."
        )
        current_question = await owner_request(
            owner_userbot, chat, f"/ai What is {alias_token}'s current synthetic plan?"
        )
        await wait_for_reply(
            owner,
            chat,
            current_question,
            owner_identity.id,
            expected_text=new_value,
        )
        print("revision_current_state=ok")

        isolated_scope = f"telegram:chat:-9999999999999-{token}"
        isolated = await memory.recall(
            scope_id=isolated_scope,
            query=f"What is the preference of {alias_token}?",
        )
        if preference_token.lower() in isolated.render(max_chars=20_000).lower():
            raise RuntimeError("Memory leaked across Hindsight banks")
        print("cross_bank_isolation=ok")

        deny = await owner_request(
            owner_userbot,
            chat,
            "/ai_deny",
            reply_to=access_target.id,
        )
        await wait_for_reply(
            owner, chat, deny, owner_identity.id, expected_text="AI access denied."
        )
        denied = await peer.send_message(peer_chat, f"/ai reply with DENIED_{token}")
        await assert_no_reply(owner, chat, denied, owner_identity.id)
        print("whitelist_deny=ok")

        disable = await owner_request(owner_userbot, chat, "/ai_memory_disable")
        await wait_for_reply(
            owner,
            chat,
            disable,
            owner_identity.id,
            expected_text="Continuous memory disabled",
        )
        print(f"completed_at={datetime.now(UTC).isoformat()}")
    finally:
        if chat is not None:
            await asyncio.sleep(1)
            created_ids = []
            async for message in owner.iter_messages(chat, limit=300):
                if message.id <= baseline_id:
                    break
                created_ids.append(message.id)
            if created_ids:
                await owner.delete_messages(chat, created_ids, revoke=True)
        saved_created_ids = []
        async for message in owner.iter_messages("me", limit=100):
            if message.id <= saved_baseline_id:
                break
            saved_created_ids.append(message.id)
        if saved_created_ids:
            await owner.delete_messages("me", saved_created_ids)
        await memory.close()
        await peer.disconnect()
        await close_owner_userbot(owner_userbot)


def main() -> None:
    apply_config()
    asyncio.run(run())


if __name__ == "__main__":
    main()
