from telethon import utils
from telethon.tl.functions.channels import CreateChannelRequest

from telefire.plugins.base import PluginMount
from telefire.telegram import TelegramCommand


class ListMessages(TelegramCommand, metaclass=PluginMount):
    command_name = "list_deleted_user_messages"

    async def _list_messages_async(self, chat):
        async for msg in self.client.iter_messages(chat):
            sender = await msg.get_sender()
            if sender and sender.deleted:
                self.logger.info(f"https://t.me/{chat}/{msg.id}: {msg.text}")

    def __call__(self, chat):
        self.set_file_handler(f"list_deleted_user_messages_for_[{chat}]")
        self.run_once(lambda: self._list_messages_async(chat))
