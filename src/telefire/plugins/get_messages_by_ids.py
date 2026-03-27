from telefire.plugins.base import PluginMount
from telefire.telegram import TelegramCommand


class Action(TelegramCommand, metaclass=PluginMount):
    command_name = "get_messages_by_ids"

    async def _get_message_by_ids_async(self, chat, ids):
        async for msg in self.client.iter_messages(chat, ids=ids):
            print(msg)

    def __call__(self, chat, *ids):
        self.run_once(lambda: self._get_message_by_ids_async(chat, ids))
