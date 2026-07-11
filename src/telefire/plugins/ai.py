from telethon import events

from telefire.ai import AIMessageHandler, AIResponder, AISettings, OpenAIChatGateway
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
        self._handler: AIMessageHandler | None = None

    def __call__(self) -> None:
        """Run the reply-based OpenAI-compatible Telegram userbot."""
        self.run_forever(setup=self._setup)

    async def _setup(self) -> None:
        owner = await self.client.get_me()
        self._handler = AIMessageHandler(owner_id=owner.id, responder=self._responder)
        self.client.add_event_handler(self._on_message, events.NewMessage())
        self.logger.info("Telegram AI userbot started")

    async def _on_message(self, event) -> None:
        if self._handler is not None:
            await self._handler.handle(event.message)
