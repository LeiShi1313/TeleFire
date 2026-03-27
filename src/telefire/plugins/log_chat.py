from telethon.sync import events
from telefire.plugins.base import PluginMount
from telefire.telegram import TelegramCommand
from telethon import utils


class LogChat(TelegramCommand, metaclass=PluginMount):
    command_name = "log_chat"

    def __call__(self, chat=None):
        def setup():
            @self.client.on(events.NewMessage)
            async def _inner(evt):
                msg = evt.message
                channel = await self.client.get_entity(msg.to_id)
                to_chat = await evt.get_chat()
                self.logger.info(f"display name:{utils.get_display_name(to_chat)}\tid:{to_chat.id}\tusername:{to_chat.username}")
                # if channel.username == chat:
                #     if msg.media:
                #         self.logger.info(msg)

        self.set_file_handler("log_chat")
        self.run_forever(setup=setup)
