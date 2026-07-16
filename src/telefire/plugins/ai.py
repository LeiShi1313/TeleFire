import asyncio
from dataclasses import dataclass
from urllib.parse import parse_qs, urlsplit

from telethon import events
from telethon import utils as telegram_utils
from telethon.errors import PremiumAccountRequiredError
from telethon.tl import functions as telegram_functions
from telethon.tl import types as telegram_types

from telefire.ai import (
    AIConversationHandler,
    AIRateLimiter,
    AIResponder,
    AISettings,
    AIStateRepository,
    PiAgentGateway,
    PromptBuilder,
    TelegramEditLimiter,
    TelegramMemoryScopeTargetResolver,
    TelegramMessageIdentityResolver,
    TelegramMessageMentionResolver,
    select_telegram_response_format,
)
from telefire.ai_attachments import TelegramAttachmentDescriber
from telefire.ai_dream import (
    ContinuousMemoryScheduler,
    ContinuousMemorySchedulerSettings,
    DreamScheduler,
    DreamSchedulerSettings,
    DreamSettings,
    TelegramDreamScanner,
    TelegramHistorySource,
)
from telefire.ai_memory import HindsightMemoryClient
from telefire.plugins.base import PluginMount
from telefire.telegram import TelegramCommand


class _SavedMemoryForwardSourceUnavailable(RuntimeError):
    pass


class _SavedMemoryLinkUnavailable(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class _TelegramMessageLink:
    username: str | None
    channel_id: int | None
    message_id: int


def _parse_positive_id(value: str, *, maximum: int) -> int | None:
    if not value.isascii() or not value.isdecimal():
        return None
    parsed = int(value)
    return parsed if 0 < parsed <= maximum else None


# https://core.telegram.org/api/links#message-links
def _parse_telegram_message_link(text: str) -> _TelegramMessageLink | None:
    candidate = text.strip()
    if any(character.isspace() for character in candidate):
        return None
    try:
        link = urlsplit(candidate)
    except ValueError:
        return None
    if link.scheme != "https" or link.netloc.casefold() != "t.me":
        return None
    query_keys = parse_qs(link.query, keep_blank_values=True)
    if any(key.casefold() == "comment" for key in query_keys):
        return None

    path = link.path.strip("/")
    segments = path.split("/") if path else []
    if any(not segment for segment in segments):
        return None

    max_message_id = (1 << 31) - 1
    if segments[:1] == ["c"]:
        if len(segments) not in {3, 4}:
            return None
        channel_id = _parse_positive_id(segments[1], maximum=(1 << 63) - 1)
        if (
            len(segments) == 4
            and _parse_positive_id(segments[2], maximum=max_message_id) is None
        ):
            return None
        message_id = _parse_positive_id(segments[-1], maximum=max_message_id)
        if channel_id is None or message_id is None:
            return None
        return _TelegramMessageLink(
            username=None,
            channel_id=channel_id,
            message_id=message_id,
        )

    if len(segments) not in {2, 3}:
        return None
    username = segments[0]
    if (
        len(username) > 64
        or not username.isascii()
        or not username.replace("_", "").isalnum()
    ):
        return None
    if (
        len(segments) == 3
        and _parse_positive_id(segments[1], maximum=max_message_id) is None
    ):
        return None
    message_id = _parse_positive_id(segments[-1], maximum=max_message_id)
    if message_id is None:
        return None
    return _TelegramMessageLink(
        username=username,
        channel_id=None,
        message_id=message_id,
    )


class TelegramAI(TelegramCommand, metaclass=PluginMount):
    command_name = "ai"
    MEMORY_STORED_REACTION = "✍"
    MEMORY_FAILED_REACTION = "👎"
    MEMORY_PROCESSING_REPLY = "Remembering..."
    MEMORY_STORED_REPLY = "Remembered."
    MEMORY_FAILED_REPLY = "Memory update failed. Forward the message again to retry."
    MEMORY_LINK_FAILED_REPLY = (
        "Memory update failed. Send the message link again to retry."
    )
    MEMORY_SOURCE_UNAVAILABLE_REPLY = (
        "Telegram hid the original source. Paste the original message link in "
        "Saved Messages to remember its reply chain."
    )
    MEMORY_LINK_UNAVAILABLE_REPLY = (
        "Memory update unavailable: the linked message could not be fetched. "
        "Make sure this account can open it and the message still exists."
    )

    def __init__(
        self,
        account: str = "default",
        session: str | None = None,
        log_level: str = "info",
    ):
        super().__init__(account=account, session=session, log_level=log_level)
        settings = AISettings.from_env()
        self._settings = settings
        self._edit_limiter = TelegramEditLimiter(
            settings.edit_cadence,
            logger=self.logger,
        )
        self._gateway = PiAgentGateway(
            settings.agent_url,
            token=settings.agent_token,
            timeout=settings.request_timeout,
        )
        self._responder: AIResponder | None = None
        self._store = AIStateRepository(settings.state_path)
        self._memory = (
            HindsightMemoryClient(
                settings.hindsight_url,
                timeout=settings.hindsight_timeout,
            )
            if settings.hindsight_url
            else None
        )
        self._handler: AIConversationHandler | None = None
        self._dream_scheduler: DreamScheduler | None = None
        self._continuous_memory_scheduler: ContinuousMemoryScheduler | None = None
        self._owner_id: int | None = None
        self._saved_memory_lock = asyncio.Lock()

    def __call__(self) -> None:
        """Run the reply-based Pi-powered Telegram userbot."""
        try:
            asyncio.run(self._run())
        except KeyboardInterrupt:
            pass

    async def _run(self) -> None:
        await self.service.connect()
        try:
            await self._setup()
            await self.service.wait_until_disconnected()
        finally:
            if self._continuous_memory_scheduler is not None:
                await self._continuous_memory_scheduler.close()
            if self._dream_scheduler is not None:
                await self._dream_scheduler.close()
            if self._memory is not None:
                await self._memory.close()
            await self._gateway.close()
            await self._store.close()
            await self.service.close()

    async def _setup(self) -> None:
        owner = await self.client.get_me()
        self._owner_id = owner.id
        response_format = select_telegram_response_format(
            is_bot_account=bool(getattr(owner, "bot", False)),
            rich_messages_available=True,
        )
        responder = AIResponder(
            self._gateway,
            max_output_chars=self._settings.max_output_chars,
            response_format=response_format,
            edit_limiter=self._edit_limiter,
            logger=self.logger,
        )
        self._responder = responder
        await self._store.connect()
        history_source = TelegramHistorySource(self.client)
        prompt_builder = PromptBuilder(
            system_prompt=self._settings.system_prompt,
            max_context_messages=self._settings.max_context_messages,
            max_context_chars=self._settings.max_context_chars,
            response_format=response_format,
            attachment_describer=TelegramAttachmentDescriber(
                self._gateway,
                logger=self.logger,
            ),
            identity_resolver=TelegramMessageIdentityResolver(
                logger=self.logger,
            ),
            mention_resolver=TelegramMessageMentionResolver(
                self.client,
                logger=self.logger,
            ),
            history_source=history_source,
        )
        dream_runner = (
            TelegramDreamScanner(
                source=history_source,
                store=self._store,
                memory=self._memory,
                prompt_builder=prompt_builder,
                settings=DreamSettings.from_env(),
                logger=self.logger,
            )
            if self._memory is not None
            else None
        )
        if dream_runner is not None:
            self._dream_scheduler = DreamScheduler(
                scanner=dream_runner,
                store=self._store,
                settings=DreamSchedulerSettings.from_env(),
                logger=self.logger,
            )
            self._continuous_memory_scheduler = ContinuousMemoryScheduler(
                scanner=dream_runner,
                store=self._store,
                settings=ContinuousMemorySchedulerSettings.from_env(),
                logger=self.logger,
            )
        self._handler = AIConversationHandler(
            owner_id=owner.id,
            responder=responder,
            store=self._store,
            prompt_builder=prompt_builder,
            rate_limiter=AIRateLimiter(
                self._store,
                cooldown_seconds=self._settings.delegated_cooldown,
            ),
            memory=self._memory,
            dream_runner=dream_runner,
            memory_scope_resolver=TelegramMemoryScopeTargetResolver(
                self.client,
                logger=self.logger,
            ),
            memory_command_delete_delay=(
                self._settings.memory_command_delete_delay
            ),
            logger=self.logger,
        )
        self.client.add_event_handler(self._on_message, events.NewMessage())
        if self._continuous_memory_scheduler is not None:
            self._continuous_memory_scheduler.start()
        if self._dream_scheduler is not None:
            self._dream_scheduler.start()
        self.logger.info("Telegram AI userbot started")

    async def _on_message(self, event) -> None:
        if self._handler is not None:
            try:
                if await self._handle_saved_memory(event.message):
                    return
                await self._handler.handle(event.message)
            except Exception:
                self.logger.exception(
                    "Telegram AI message handling failed (chat_id=%s, message_id=%s)",
                    event.message.chat_id,
                    event.message.id,
                )

    async def _handle_saved_memory(self, message) -> bool:
        if not self._is_saved_messages_message(message):
            return False
        forward = getattr(message, "fwd_from", None)
        link = (
            None
            if forward is not None
            else _parse_telegram_message_link(getattr(message, "raw_text", "") or "")
        )
        if forward is None and link is None:
            return False

        assert self._owner_id is not None
        async with self._saved_memory_lock:
            status_message = None
            try:
                if await self._store.is_memory_forward_processed(
                    owner_id=self._owner_id,
                    saved_message_id=message.id,
                ):
                    await self._set_saved_memory_reaction(
                        message,
                        self.MEMORY_STORED_REACTION,
                    )
                    return True

                status_message = await self._start_saved_memory_status(message)
                if link is not None:
                    source, source_chat_id = await self._resolve_memory_link(link)
                    source_message_id = link.message_id
                else:
                    source_peer = getattr(forward, "saved_from_peer", None)
                    source_message_id = getattr(forward, "saved_from_msg_id", None)
                    if source_peer is None or not isinstance(source_message_id, int):
                        raise _SavedMemoryForwardSourceUnavailable(
                            "forward has no Saved Messages source pointer"
                        )

                    source = await self.client.get_messages(
                        source_peer,
                        ids=source_message_id,
                    )
                    source_chat_id = getattr(source, "chat_id", None)
                    if source is None or not isinstance(source_chat_id, int):
                        raise _SavedMemoryForwardSourceUnavailable(
                            "original Telegram message is unavailable"
                        )
                if not await self._handler.remember_reply_chain(source):
                    raise RuntimeError("reply chain has no ingestible human content")

                await self._store.record_memory_forward(
                    owner_id=self._owner_id,
                    saved_message_id=message.id,
                    source_chat_id=source_chat_id,
                    source_message_id=source_message_id,
                )
            except Exception as exc:
                self.logger.warning(
                    "Saved Messages memory ingest failed "
                    "(saved_message_id=%s, error=%s): %s",
                    message.id,
                    type(exc).__name__,
                    exc,
                )
                await self._set_saved_memory_reaction(
                    message,
                    self.MEMORY_FAILED_REACTION,
                )
                if isinstance(exc, _SavedMemoryForwardSourceUnavailable):
                    reply = self.MEMORY_SOURCE_UNAVAILABLE_REPLY
                elif isinstance(exc, _SavedMemoryLinkUnavailable):
                    reply = self.MEMORY_LINK_UNAVAILABLE_REPLY
                elif link is not None:
                    reply = self.MEMORY_LINK_FAILED_REPLY
                else:
                    reply = self.MEMORY_FAILED_REPLY
                await self._finish_saved_memory_status(
                    message,
                    status_message,
                    reply,
                )
                return True

            self.logger.info(
                "Saved Messages memory ingested "
                "(saved_message_id=%s, source_chat_id=%s, source_message_id=%s)",
                message.id,
                source_chat_id,
                source_message_id,
            )
            await self._set_saved_memory_reaction(
                message,
                self.MEMORY_STORED_REACTION,
            )
            await self._finish_saved_memory_status(
                message,
                status_message,
                self.MEMORY_STORED_REPLY,
            )
            return True

    async def _resolve_memory_link(
        self,
        link: _TelegramMessageLink,
    ) -> tuple[object, int]:
        try:
            source_peer = await self._resolve_memory_link_peer(link)
            expected_chat_id = telegram_utils.get_peer_id(source_peer)
            _, peer_type = telegram_utils.resolve_id(expected_chat_id)
            if peer_type is not telegram_types.PeerChannel:
                raise ValueError("message link did not resolve to a channel")
            source = await self.client.get_messages(
                source_peer,
                ids=link.message_id,
            )
        except Exception as exc:
            raise _SavedMemoryLinkUnavailable(str(exc)) from exc

        source_chat_id = getattr(source, "chat_id", None)
        source_message_id = getattr(source, "id", None)
        if (
            source is None
            or source_chat_id != expected_chat_id
            or source_message_id != link.message_id
        ):
            raise _SavedMemoryLinkUnavailable("linked Telegram message is unavailable")
        return source, source_chat_id

    async def _resolve_memory_link_peer(self, link: _TelegramMessageLink):
        if link.username is not None:
            return await self.client.get_input_entity(link.username)

        assert link.channel_id is not None
        peer = telegram_types.PeerChannel(link.channel_id)
        try:
            return await self.client.get_input_entity(peer)
        except ValueError as cache_error:
            expected_chat_id = telegram_utils.get_peer_id(peer)
            self.logger.info(
                "Private message-link peer missing from entity cache; "
                "searching dialogs (chat_id=%s)",
                expected_chat_id,
            )
            async for dialog in self.client.iter_dialogs():
                if dialog.id == expected_chat_id:
                    return dialog.input_entity
            raise cache_error

    def _is_saved_messages_message(self, message) -> bool:
        peer = getattr(message, "peer_id", None)
        destination_id = (
            telegram_utils.get_peer_id(peer)
            if peer is not None
            else getattr(message, "chat_id", None)
        )
        return bool(self._owner_id is not None and destination_id == self._owner_id)

    async def _set_saved_memory_reaction(self, message, reaction: str) -> bool:
        try:
            peer = await message.get_input_chat()
            await self.client(
                telegram_functions.messages.SendReactionRequest(
                    peer=peer,
                    msg_id=message.id,
                    reaction=[telegram_types.ReactionEmoji(emoticon=reaction)],
                )
            )
            return True
        except PremiumAccountRequiredError:
            self.logger.info(
                "Saved Messages memory marker unavailable for non-Premium account "
                "(saved_message_id=%s)",
                message.id,
            )
        except Exception as exc:
            self.logger.warning(
                "Saved Messages memory marker failed "
                "(saved_message_id=%s, error=%s): %s",
                message.id,
                type(exc).__name__,
                exc,
            )
        return False

    async def _start_saved_memory_status(self, message):
        try:
            return await message.reply(self.MEMORY_PROCESSING_REPLY, parse_mode=None)
        except Exception as exc:
            self.logger.warning(
                "Saved Messages memory processing reply failed "
                "(saved_message_id=%s, error=%s): %s",
                message.id,
                type(exc).__name__,
                exc,
            )
            return None

    async def _finish_saved_memory_status(
        self,
        message,
        status_message,
        reply: str,
    ) -> None:
        if status_message is not None:
            try:
                if await self._edit_limiter.run(
                    lambda: status_message.edit(reply, parse_mode=None),
                    wait=True,
                ):
                    return
            except Exception as exc:
                self.logger.warning(
                    "Saved Messages memory status edit failed "
                    "(saved_message_id=%s, error=%s): %s",
                    message.id,
                    type(exc).__name__,
                    exc,
                )
        try:
            await message.reply(reply, parse_mode=None)
        except Exception as exc:
            self.logger.warning(
                "Saved Messages memory final reply failed "
                "(saved_message_id=%s, error=%s): %s",
                message.id,
                type(exc).__name__,
                exc,
            )
