import os
import re
import pickle
import random
import asyncio
import traceback
from collections import defaultdict
from telethon import utils as telethon_utils
from telethon.sync import events
from plugins.base import Telegram, PluginMount
from utils import get_url


class Action(Telegram, metaclass=PluginMount):
    command_name = "auto_lottery"

    def __call__(self):

        @self._client.on(events.NewMessage(from_users=['tglottery_bot', 'caomeixigua']))
        async def _inner(evt):
            msg = evt.message
            try:
                m = pattern.search(msg.text)
                if m:
                    await asyncio.sleep(random.randint(10,20))
                    await self._client.send_message(msg.to_id, m.group(1))
                    self._logger.info(f"Participated:\ {msg.text}")
            except Exception as e:
                traceback.print_exc()
        
        pattern = re.compile(r'群内发送关键词 (.*) 参与抽奖')
        self._set_file_handler("auto_lottery")
        self._logger.info(f"Auto lottery start.")
        self._client.start()
        self._client.run_until_disconnected()
