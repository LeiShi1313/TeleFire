import os
import pickle
import random
import traceback
from collections import defaultdict
from telethon import utils as telethon_utils
from telethon.sync import events
from telefire.plugins.base import Matrix, PluginMount
from mautrix.types import EventType, Filter, RoomFilter, MessageEvent


class Action(Matrix, metaclass=PluginMount):
    command_name = "matrix_plus_mode"

    def __call__(self,):

        @self._client.on(EventType.ROOM_MESSAGE)
        async def _inner(event: MessageEvent):
            if event.sender != self.user_id:
                return
            print(event)

        self.start()

