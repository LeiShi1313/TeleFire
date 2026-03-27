import os
import pickle
import random
import traceback
from collections import defaultdict
from telethon import utils as telethon_utils
from telethon.sync import events
from telefire.plugins.base import Telegram, PluginMount
from telefire.utils import get_url


class Action(Telegram, metaclass=PluginMount):
    command_name = "auto_reply"

    def __call__(self, regex, reply, chat=None, from_sender=None):

        @self._client.on(events.NewMessage(pattern=regex))
        async def _inner(evt):
            msg = evt.message
            try:
                to_chat = await evt.get_chat()
                sender = await msg.get_sender()
                if chat is not None and to_chat.username != chat and to_chat.id != chat:
                    return
                if from_sender is not None and sender.username != from_sender and sender.id != from_sender:
                    return
                await self._client.send_message(to_chat, reply)
            except Exception as e:
                traceback.print_exc()
        
        
        self._set_file_handler("auto_reply")
        self._logger.info(f"Auto reply start for regex: {regex}, reply: {reply}, chat: {chat}, from: {from_sender}")
        self._run_forever_command()
