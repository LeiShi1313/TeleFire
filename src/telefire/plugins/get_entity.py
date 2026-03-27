import re
import pickle
import traceback
from telethon import utils
from telethon.tl.functions.channels import CreateChannelRequest
from telethon.tl.types import TypeMessagesFilter

from telefire.plugins.base import PluginMount
from telefire.telegram import TelegramCommand


class Action(TelegramCommand, metaclass=PluginMount):
    command_name = 'get_entity'

    async def _get_entity_async(self, entity):
        print(await self._get_entity(entity))

    def __call__(self, entity):
        self.run_telegram(self._get_entity_async(entity))
