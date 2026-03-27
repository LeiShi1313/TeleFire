from telethon.sync import events
from telefire.plugins.base import PluginMount
from telefire.telegram import TelegramCommand


class GetAllChats(TelegramCommand, metaclass=PluginMount):
    command_name = "get_all_chats"

    def __call__(self):
        async def _get_all_chats():
            async for dialog in self.client.iter_dialogs():
                self.logger.info("{:>14}: {}".format(dialog.id, dialog.title))

        self.run_once(_get_all_chats)
