from telethon.sync import events
from plugins.base import Telegram, PluginMount


class GetAllChats(Telegram, metaclass=PluginMount):
    command_name = 'get_all_chats'

    def __call__(self):
        async def _get_all_chats(self):
            async for dialog in self._client.iter_dialogs():
                self._logger.info('{:>14}: {}'.format(dialog.id, dialog.title))

        with self._client:
            self._client.loop.run_until_complete(_get_all_chats(self))

