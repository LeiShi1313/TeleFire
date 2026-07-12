from __future__ import annotations

import asyncio
from io import BytesIO
import os
from uuid import uuid4

import aiohttp
from PIL import Image, ImageDraw
from telethon import TelegramClient, utils

from telefire.config import apply_config
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
    raise RuntimeError("Memory service did not become healthy")


async def direct_replies(client, chat, message_id: int, sender_id: int):
    replies = []
    async for message in client.iter_messages(chat, limit=80):
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
    expected_text: str | None = None,
    timeout: float = 120,
):
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        replies = await direct_replies(client, chat, trigger.id, sender_id)
        if len(replies) > 1:
            raise RuntimeError("AI created more than one direct reply")
        if replies:
            reply = replies[0]
            text = (reply.raw_text or "").strip()
            if text == "Thinking...":
                await asyncio.sleep(0.25)
                continue
            if text.startswith("AI request failed"):
                raise RuntimeError("AI provider failed during live E2E")
            if text.startswith("AI rate limit active"):
                raise RuntimeError("Live E2E request unexpectedly hit the rate limit")
            if expected_text and expected_text.lower() not in text.lower():
                await asyncio.sleep(0.25)
                continue
            return reply
        await asyncio.sleep(0.25)
    raise RuntimeError("Timed out waiting for the expected Telegram reply")


async def assert_no_reply(client, chat, trigger, sender_id: int, timeout: float = 4):
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if await direct_replies(client, chat, trigger.id, sender_id):
            raise RuntimeError("A request expected to be silent received a reply")
        await asyncio.sleep(0.25)


async def wait_for_saved_memory(
    memory_url: str,
    subject_id: str,
    scope_id: str,
    values: tuple[str, ...],
    *,
    timeout: float = 180,
) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        results = await asyncio.gather(
            *(
                get_profile(memory_url, subject_id, value, scope_id=scope_id)
                for value in values
            )
        )
        if all(
            value in (result.get("rendered") or "")
            for value, result in zip(values, results, strict=True)
        ):
            return
        await asyncio.sleep(1)
    raise RuntimeError("Timed out waiting for Saved Messages memory ingestion")


async def wait_for_delegated_cooldown() -> None:
    cooldown = float(os.environ.get("TELEFIRE_AI_DELEGATED_COOLDOWN", "30"))
    await asyncio.sleep(max(cooldown, 0) + 0.5)


async def get_profile(memory_url: str, subject_id: str, query: str, scope_id=None):
    body = {"subject_id": subject_id, "query": query}
    if scope_id is not None:
        body["scope_id"] = scope_id
    async with aiohttp.ClientSession() as session:
        async with session.post(
            f"{memory_url.rstrip('/')}/v1/memory/augment",
            json=body,
        ) as response:
            if response.status != 200:
                raise RuntimeError("Memory augmentation failed during live E2E")
            payload = await response.json()
    if not isinstance(payload, dict):
        raise RuntimeError("Memory service returned an invalid live E2E response")
    return payload


async def run() -> None:
    if os.environ.get("TELEFIRE_RUN_E2E") != "1":
        raise RuntimeError("Set TELEFIRE_RUN_E2E=1 to run the mutating live test")
    chat_raw = os.environ.get("TELEFIRE_E2E_CHAT_ID", "")
    if not chat_raw.startswith("-100"):
        raise RuntimeError(
            "TELEFIRE_E2E_CHAT_ID must be an explicit numeric megagroup ID"
        )
    chat_id = int(chat_raw)
    allowed_ids = {
        int(value)
        for value in os.environ.get("TELEFIRE_AI_ALLOWED_CHAT_IDS", "").split(",")
        if value.strip()
    }
    if allowed_ids != {chat_id}:
        raise RuntimeError("Live userbot must be hard-allowlisted to only the E2E chat")

    controller_account = os.environ.get(
        "TELEFIRE_E2E_CONTROLLER_SESSION", "ai_e2e_peer"
    )
    peer_account = os.environ.get("TELEFIRE_E2E_PEER_SESSION", "ai_e2e_peer2")
    memory_url = os.environ.get("TELEFIRE_MEMORY_URL", "").rstrip("/")
    if not memory_url:
        raise RuntimeError("TELEFIRE_MEMORY_URL is required")
    await wait_for_health(memory_url)

    owner = await connect_account(controller_account)
    peer = await connect_account(peer_account)
    baseline_id = 0
    chat = None
    saved_message_ids = []
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
        token = uuid4().hex[:8]

        unauthorized = await peer.send_message(
            peer_chat,
            f"/ai reply with UNAUTHORIZED_{token}",
        )
        await assert_no_reply(owner, chat, unauthorized, owner_identity.id)
        print("unauthorized_silence=ok")

        access_target = await peer.send_message(peer_chat, f"access-target-{token}")
        allow = await owner.send_message(chat, "/ai_allow", reply_to=access_target.id)
        await wait_for_reply(
            owner,
            chat,
            allow,
            owner_identity.id,
            expected_text="AI access allowed.",
        )
        print("whitelist_allow=ok")

        root_token = f"TELEFIRE_ROOT_{token}"
        root = await peer.send_message(
            peer_chat,
            f"/ai Reply with exactly {root_token}",
        )
        root_answer = await wait_for_reply(
            owner,
            chat,
            root,
            owner_identity.id,
            expected_text=root_token,
        )
        print("streamed_root=ok")
        await wait_for_delegated_cooldown()

        follow_token = f"TELEFIRE_FOLLOW_{token}"
        follow = await peer.send_message(
            peer_chat,
            f"Reply with exactly {follow_token}",
            reply_to=root_answer.id,
        )
        await wait_for_reply(
            owner,
            chat,
            follow,
            owner_identity.id,
            expected_text=follow_token,
        )
        print("continuation=ok")
        await wait_for_delegated_cooldown()

        fork_token = f"TELEFIRE_FORK_{token}"
        fork = await peer.send_message(
            peer_chat,
            f"Reply with exactly {fork_token}",
            reply_to=root_answer.id,
        )
        await wait_for_reply(
            owner,
            chat,
            fork,
            owner_identity.id,
            expected_text=fork_token,
        )
        print("fork=ok")
        await wait_for_delegated_cooldown()

        calculation = await peer.send_message(
            peer_chat,
            "/ai Use code_exec to sum the integers from 1 through 100. Include 5050 in the answer.",
        )
        await wait_for_reply(
            owner,
            chat,
            calculation,
            owner_identity.id,
            expected_text="5050",
        )
        print("delegated_code_exec=ok")
        await wait_for_delegated_cooldown()

        web_search = await peer.send_message(
            peer_chat,
            "/ai Use web_search to find the official Python website. Include python.org in the answer.",
        )
        await wait_for_reply(
            owner,
            chat,
            web_search,
            owner_identity.id,
            expected_text="python.org",
        )
        print("delegated_web_search=ok")
        await wait_for_delegated_cooldown()

        image_token = f"MEDIA_{token}"
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
            caption=f"Synthetic attachment {token}",
        )
        attachment_question = await peer.send_message(
            peer_chat,
            (
                "/ai Describe the replied image and include both the dominant color "
                f"and the visible token {image_token}."
            ),
            reply_to=attachment.id,
        )
        attachment_answer = await wait_for_reply(
            owner,
            chat,
            attachment_question,
            owner_identity.id,
            expected_text=image_token,
        )
        if "red" not in (attachment_answer.raw_text or "").lower():
            raise RuntimeError("Attachment answer omitted the dominant color")
        print("reply_attachment_description=ok")
        await wait_for_delegated_cooldown()

        current_image_token = f"CURRENT_MEDIA_{token}"
        current_image_file = BytesIO()
        current_image_file.name = f"telefire-current-{token}.png"
        current_image = Image.new("RGB", (640, 320), "white")
        current_drawing = ImageDraw.Draw(current_image)
        current_drawing.rectangle((30, 30, 280, 290), fill="red")
        current_drawing.text((320, 140), current_image_token, fill="black")
        current_image.save(current_image_file, format="PNG")
        current_image_file.seek(0)
        current_attachment = await peer.send_file(
            peer_chat,
            current_image_file,
            caption=(
                "/ai Describe this image and include both the dominant color and "
                f"the visible token {current_image_token}."
            ),
        )
        current_attachment_answer = await wait_for_reply(
            owner,
            chat,
            current_attachment,
            owner_identity.id,
            expected_text=current_image_token,
        )
        if "red" not in (current_attachment_answer.raw_text or "").lower():
            raise RuntimeError("Current attachment answer omitted the dominant color")
        print("current_attachment_description=ok")
        await wait_for_delegated_cooldown()

        subject_id = f"telegram:user:{peer_identity.id}"
        scope_id = f"telegram:chat:{chat_id}"

        saved_root_token = f"SAVED_ROOT_{token}"
        saved_target_token = f"SAVED_TARGET_{token}"
        saved_root = await peer.send_message(
            peer_chat,
            f"My synthetic saved-memory root is {saved_root_token}.",
        )
        saved_target = await peer.send_message(
            peer_chat,
            f"My synthetic saved-memory follow-up is {saved_target_token}.",
            reply_to=saved_root.id,
        )
        saved_forward = await owner.forward_messages("me", saved_target)
        saved_message_ids.append(saved_forward.id)
        await wait_for_saved_memory(
            memory_url,
            subject_id,
            scope_id,
            (saved_root_token, saved_target_token),
        )
        retained_forward = await owner.get_messages("me", ids=saved_forward.id)
        if retained_forward.id != saved_forward.id:
            raise RuntimeError(
                "Saved Messages forward was not retained after ingestion"
            )
        print("saved_messages_memory_chain=ok")

        coffee = f"coffee-{token}"
        coffee_evidence = await peer.send_message(
            peer_chat,
            f"My synthetic preference is {coffee}.",
        )
        add = await owner.send_message(
            chat,
            (
                "/ai_memory Replace the Subject Profile with exactly this Markdown: "
                f"# User Profile\n\n- Synthetic preference: {coffee}."
            ),
            reply_to=coffee_evidence.id,
        )
        await wait_for_reply(
            owner,
            chat,
            add,
            owner_identity.id,
            expected_text="Memory updated.",
        )
        added_profile = await get_profile(memory_url, subject_id, coffee)
        if coffee not in (added_profile.get("profile") or ""):
            raise RuntimeError("Telegram memory add did not update the target profile")
        print("memory_add=ok")

        tea = f"tea-{token}"
        tea_evidence = await peer.send_message(
            peer_chat,
            f"My corrected synthetic preference is {tea}.",
        )
        correct = await owner.send_message(
            chat,
            (
                "/ai_memory Replace the Subject Profile with exactly this Markdown and "
                f"remove {coffee}: # User Profile\n\n- Synthetic preference: {tea}."
            ),
            reply_to=tea_evidence.id,
        )
        await wait_for_reply(
            owner,
            chat,
            correct,
            owner_identity.id,
            expected_text="Memory updated.",
        )
        corrected_profile = await get_profile(memory_url, subject_id, tea)
        profile_text = corrected_profile.get("profile") or ""
        if tea not in profile_text or coffee in profile_text:
            raise RuntimeError("Telegram memory correction did not replace the profile")
        print("memory_correct=ok")

        memory_question = await peer.send_message(
            peer_chat,
            f"/ai What is my synthetic preference? Include {tea} in the answer.",
        )
        await wait_for_reply(
            owner,
            chat,
            memory_question,
            owner_identity.id,
            expected_text=tea,
        )
        print("requester_profile_augmentation=ok")
        await asyncio.sleep(5)

        forget = await owner.send_message(
            chat,
            (
                f"/ai_memory Remove every mention of {tea}, {coffee}, {image_token}, "
                f"{current_image_token}, {saved_root_token}, and {saved_target_token} "
                "from the Subject Profile and suppress all matching derived facts and "
                "episodes."
            ),
            reply_to=memory_question.id,
        )
        await wait_for_reply(
            owner,
            chat,
            forget,
            owner_identity.id,
            expected_text="Memory updated.",
        )
        forgotten = await get_profile(memory_url, subject_id, tea, scope_id=scope_id)
        forgotten_text = forgotten.get("rendered") or ""
        if tea in forgotten_text or coffee in forgotten_text:
            raise RuntimeError(
                "Telegram memory forget left matching retrievable content"
            )
        for forgotten_token in (
            image_token,
            current_image_token,
            saved_root_token,
            saved_target_token,
        ):
            token_memory = await get_profile(
                memory_url,
                subject_id,
                forgotten_token,
                scope_id=scope_id,
            )
            if forgotten_token in (token_memory.get("rendered") or ""):
                raise RuntimeError(
                    "Telegram memory forget left retrievable synthetic content"
                )
        print("memory_forget=ok")

        deny = await owner.send_message(chat, "/ai_deny", reply_to=access_target.id)
        await wait_for_reply(
            owner,
            chat,
            deny,
            owner_identity.id,
            expected_text="AI access denied.",
        )
        denied = await peer.send_message(
            peer_chat,
            f"/ai reply with DENIED_{token}",
        )
        await assert_no_reply(owner, chat, denied, owner_identity.id)
        print("whitelist_deny=ok")
    finally:
        if chat is not None:
            await asyncio.sleep(1)
            created_ids = []
            async for message in owner.iter_messages(chat, limit=200):
                if message.id <= baseline_id:
                    break
                created_ids.append(message.id)
            if created_ids:
                await owner.delete_messages(chat, created_ids, revoke=True)
        if saved_message_ids:
            await owner.delete_messages("me", saved_message_ids)
        await asyncio.gather(owner.disconnect(), peer.disconnect())


def main() -> None:
    apply_config()
    asyncio.run(run())


if __name__ == "__main__":
    main()
