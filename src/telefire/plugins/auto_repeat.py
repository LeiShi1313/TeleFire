import os
import pickle
import random
import traceback
from collections import defaultdict, deque
from telethon import utils as telethon_utils
from telethon.sync import events
from telefire.plugins.base import Telegram, PluginMount
from telefire.utils import get_url


class Action(Telegram, metaclass=PluginMount):
    command_name = "auto_repeat"

    def __call__(self, chat):
        self.pre = None

        @self._client.on(events.NewMessage(chats=[chat]))
        async def _inner(evt):
            msg = evt.message
            try:
                if self.pre is not None and msg.text == self.pre.text and msg.from_id != self.pre.from_id:
                    await self._client.send_message(msg.to_id, f"{msg.text}")
                self.pre = msg
            except Exception as e:
                traceback.print_exc()
        
        self._set_file_handler("auto_repeat")
        self._logger.info(f"Auto reply repeat for chat: {chat}")
        self._run_forever_command()
