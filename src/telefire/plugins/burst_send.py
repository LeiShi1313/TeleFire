import os
import pickle
import random
import asyncio
import traceback
from collections import defaultdict
from telethon import utils as telethon_utils
from telethon.sync import events
from telefire.plugins.base import PluginMount
from telefire.telegram import TelegramCommand
from telefire.utils import get_url


class Action(TelegramCommand, metaclass=PluginMount):
    command_name = "burst_send"

    def __call__(self, chat, msg, times=-1, interval=0):
        async def burst_send(chat, msg, times, interval):
            try:
                while times:
                    await self.client.send_message(chat, msg)
                    if times > 0:
                        times -= 1
                    if interval > 0:
                        await asyncio.sleep(interval)
            except Exception as e:
                traceback.print_exc()
        
        
        self.set_file_handler("auto_reply")
        self.logger.info(f"Burst send started for chat: {chat}, msg: {msg}, times: {times}, interval: {interval}")
        self.run_once(lambda: burst_send(chat, msg, times, interval))
