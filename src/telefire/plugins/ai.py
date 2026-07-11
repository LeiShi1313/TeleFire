from telethon import events

import asyncio

from telefire.ai import (
    AIConversationHandler,
    AIRateLimiter,
    AIResponder,
    AISettings,
    AIStateRepository,
    OpenAIChatGateway,
    PromptBuilder,
)
from telefire.ai_memory import HTTPMemoryClient
from telefire.plugins.base import PluginMount
from telefire.telegram import TelegramCommand


class TelegramAI(TelegramCommand, metaclass=PluginMount):
    command_name = "ai"

    def __init__(
        self,
        account: str = "default",
        session: str | None = None,
        log_level: str = "info",
    ):
        super().__init__(account=account, session=session, log_level=log_level)
        settings = AISettings.from_env()
        self._responder = AIResponder(
            OpenAIChatGateway(settings),
            system_prompt=settings.system_prompt,
            edit_cadence=settings.edit_cadence,
            max_output_chars=settings.max_output_chars,
            logger=self.logger,
        )
        self._settings = settings
        self._store = AIStateRepository(settings.state_path)
        self._memory = (
            HTTPMemoryClient(settings.memory_url, timeout=settings.memory_timeout)
            if settings.memory_url
            else None
        )
        self._handler: AIConversationHandler | None = None

    def __call__(self) -> None:
        """Run the reply-based OpenAI-compatible Telegram userbot."""
        asyncio.run(self._run())

    async def _run(self) -> None:
        await self.service.connect()
        try:
            await self._setup()
            await self.service.wait_until_disconnected()
        finally:
            if self._memory is not None:
                await self._memory.close()
            await self._store.close()
            await self.service.close()

    async def _setup(self) -> None:
        owner = await self.client.get_me()
        await self._store.connect()
        self._handler = AIConversationHandler(
            owner_id=owner.id,
            responder=self._responder,
            store=self._store,
            prompt_builder=PromptBuilder(
                system_prompt=self._settings.system_prompt,
                max_context_messages=self._settings.max_context_messages,
                max_context_chars=self._settings.max_context_chars,
            ),
            rate_limiter=AIRateLimiter(
                self._store,
                cooldown_seconds=self._settings.delegated_cooldown,
            ),
            memory=self._memory,
            logger=self.logger,
        )
        self.client.add_event_handler(self._on_message, events.NewMessage())
        self.logger.info("Telegram AI userbot started")

    async def _on_message(self, event) -> None:
        if self._handler is not None:
            await self._handler.handle(event.message)
