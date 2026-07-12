import asyncio

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
    TelegramMessageIdentityResolver,
    select_telegram_response_format,
)
from telefire.ai_attachments import TelegramAttachmentDescriber
from telefire.ai_memory import HTTPMemoryClient
from telefire.plugins.base import PluginMount
from telefire.telegram import TelegramCommand


class _SavedMemorySourceUnavailable(RuntimeError):
    pass


class TelegramAI(TelegramCommand, metaclass=PluginMount):
    command_name = "ai"
    MEMORY_STORED_REACTION = "✍"
    MEMORY_FAILED_REACTION = "👎"
    MEMORY_FAILED_REPLY = "Memory update failed. Forward the message again to retry."
    MEMORY_SOURCE_UNAVAILABLE_REPLY = (
        "Memory update unavailable: Telegram did not expose the original message, "
        "so its reply chain cannot be traced."
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
        self._gateway = PiAgentGateway(
            settings.agent_url,
            token=settings.agent_token,
            timeout=settings.request_timeout,
        )
        self._responder: AIResponder | None = None
        self._store = AIStateRepository(settings.state_path)
        self._memory = (
            HTTPMemoryClient(settings.memory_url, timeout=settings.memory_timeout)
            if settings.memory_url
            else None
        )
        self._handler: AIConversationHandler | None = None
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
            edit_cadence=self._settings.edit_cadence,
            max_output_chars=self._settings.max_output_chars,
            response_format=response_format,
            logger=self.logger,
        )
        self._responder = responder
        await self._store.connect()
        self._handler = AIConversationHandler(
            owner_id=owner.id,
            responder=responder,
            store=self._store,
            prompt_builder=PromptBuilder(
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
            ),
            rate_limiter=AIRateLimiter(
                self._store,
                cooldown_seconds=self._settings.delegated_cooldown,
            ),
            memory=self._memory,
            logger=self.logger,
            allowed_chat_ids=self._settings.allowed_chat_ids,
        )
        self.client.add_event_handler(self._on_message, events.NewMessage())
        self.logger.info("Telegram AI userbot started")

    async def _on_message(self, event) -> None:
        if self._handler is not None:
            try:
                if await self._handle_saved_memory_forward(event.message):
                    return
                await self._handler.handle(event.message)
            except Exception:
                self.logger.exception(
                    "Telegram AI message handling failed (chat_id=%s, message_id=%s)",
                    event.message.chat_id,
                    event.message.id,
                )

    async def _handle_saved_memory_forward(self, message) -> bool:
        if not self._is_saved_messages_forward(message):
            return False
        assert self._owner_id is not None
        async with self._saved_memory_lock:
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

                forward = message.fwd_from
                source_peer = getattr(forward, "saved_from_peer", None)
                source_message_id = getattr(forward, "saved_from_msg_id", None)
                if source_peer is None or not isinstance(source_message_id, int):
                    raise _SavedMemorySourceUnavailable(
                        "forward has no Saved Messages source pointer"
                    )

                source = await self.client.get_messages(
                    source_peer,
                    ids=source_message_id,
                )
                source_chat_id = getattr(source, "chat_id", None)
                if source is None or not isinstance(source_chat_id, int):
                    raise _SavedMemorySourceUnavailable(
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
                reply = (
                    self.MEMORY_SOURCE_UNAVAILABLE_REPLY
                    if isinstance(exc, _SavedMemorySourceUnavailable)
                    else self.MEMORY_FAILED_REPLY
                )
                await self._reply_saved_memory_failure(message, reply)
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
            return True

    def _is_saved_messages_forward(self, message) -> bool:
        peer = getattr(message, "peer_id", None)
        destination_id = (
            telegram_utils.get_peer_id(peer)
            if peer is not None
            else getattr(message, "chat_id", None)
        )
        return bool(
            self._owner_id is not None
            and destination_id == self._owner_id
            and getattr(message, "fwd_from", None) is not None
        )

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

    async def _reply_saved_memory_failure(self, message, reply: str) -> None:
        try:
            await message.reply(reply, parse_mode=None)
        except Exception as exc:
            self.logger.warning(
                "Saved Messages memory failure reply failed "
                "(saved_message_id=%s, error=%s): %s",
                message.id,
                type(exc).__name__,
                exc,
            )
