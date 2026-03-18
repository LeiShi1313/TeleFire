import os
import pickle
import random
import traceback
from collections import defaultdict
from telethon import utils as telethon_utils
from telethon.sync import events
from plugins.base import Matrix, PluginMount
from mautrix.types import EventType, Filter, RoomFilter


class Action(Matrix, metaclass=PluginMount):
    command_name = "matrix_list_rooms"

    def __call__(self):

        async def _inner():
            rooms = await self._client.get_joined_rooms()
            for room in rooms:
                name = await self.get_room_displayname(room)
                self._logger.info(f"{room}: {name}")


        self.login()
        self.loop.run_until_complete(_inner())

