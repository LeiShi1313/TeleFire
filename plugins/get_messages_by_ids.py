import re
import pickle
import traceback
from telethon import utils
from telethon.tl.functions.channels import CreateChannelRequest
from telethon.tl.types import TypeMessagesFilter

from plugins.base import Telegram, PluginMount


class Action(Telegram, metaclass=PluginMount):
    command_name = 'get_messages_by_ids'

    async def _get_message_by_ids_async(self, chat, ids):
        async for msg in self._client.iter_messages(chat, ids=ids):
            print(msg)

    def __call__(self, chat, *ids):
        with self._client:
            self._client.loop.run_until_complete(
                    self._get_message_by_ids_async(chat, ids))
