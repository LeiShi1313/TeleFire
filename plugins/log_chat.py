from telethon.sync import events
from plugins.base import Telegram, PluginMount
from telethon import utils


class LogChat(Telegram, metaclass=PluginMount):
    command_name = 'log_chat'

    def __call__(self, chat=None):
        @self._client.on(events.NewMessage)
        async def _inner(evt):
            msg = evt.message
            channel = await self._client.get_entity(msg.to_id)
            to_chat = await evt.get_chat()
            self._logger.info(f"display name:{utils.get_display_name(to_chat)}\tid:{to_chat.id}\tusername:{to_chat.username}")
            # if channel.username == chat:
            #     if msg.media:
            #         self._logger.info(msg)

        self._set_file_handler('log_chat')
        self._client.start()
        self._client.run_until_disconnected()
