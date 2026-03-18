import re
import pickle
import traceback
from telethon import utils
from telethon.tl.functions.channels import CreateChannelRequest
from telethon.tl.types import TypeMessagesFilter

from plugins.base import Telegram, PluginMount


class Action(Telegram, metaclass=PluginMount):
    command_name = 'get_entity'

    async def _get_entity_async(self, entity):
        print(await self._get_entity(entity))

    def __call__(self, entity):
        with self._client:
            self._client.loop.run_until_complete(
                self._get_entity_async(entity))
