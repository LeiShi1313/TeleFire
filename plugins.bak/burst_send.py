import os
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
    command_name = "burst_send"

    def __call__(self, chat, msg, times=-1, interval=0):
        async def burst_send(chat, msg, times, interval):
            try:
                while times:
                    await self._client.send_message(chat, msg)
                    if times > 0:
                        times -= 1
                    if interval > 0:
                        await asyncio.sleep(interval)
            except Exception as e:
                traceback.print_exc()
        
        
        self._set_file_handler("auto_reply")
        self._logger.info(f"Burst send started for chat: {chat}, msg: {msg}, times: {times}, interval: {interval}")
        with self._client:
            self._client.loop.run_until_complete(burst_send(chat, msg, times, interval))
