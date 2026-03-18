from telethon import utils
from telethon.tl.functions.channels import CreateChannelRequest

from plugins.base import Telegram, PluginMount


class ListMessages(Telegram, metaclass=PluginMount):
    command_name = 'list_deleted_user_messages'

    async def _list_messages_async(self, chat):
        async for msg in self._client.iter_messages(chat):
            sender = await msg.get_sender()
            if sender and sender.deleted:
                self._logger.info(f'https://t.me/{chat}/{msg.id}: {msg.text}')

    def __call__(self, chat):
        self._set_file_handler(f'list_deleted_user_messages_for_[{chat}]')
        with self._client:
            self._client.loop.run_until_complete(
                    self._list_messages_async(chat))
