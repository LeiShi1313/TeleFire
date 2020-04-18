from telethon.sync import events
from plugins.base import Telegram


class GetAllChats(Telegram):
    name = 'get_all_chats'

    async def _get_all_chats(self):
        async for dialog in self._client.iter_dialogs():
            self._logger.info('{:>14}: {}'.format(dialog.id, dialog.title))

    def action(self):
        with self._client:
            self._client.loop.run_until_complete(self._get_all_chats())

