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
    command_name = "auto_frds"

    def __call__(self, regex, reply, chat=None, sender=None):

        @self._client.on(events.NewMessage(pattern=regex))
        async def _inner(evt):
            msg = evt.message
            try:
                from_chat = await evt.get_chat()
                from_sender = await msg.get_sender()
                if chat is not None and (await self._get_entity(chat)).id != from_chat.id:
                    return
                if sender is not None and (await self._get_entity(sender)).id != from_sender.id:
                    return

                date = msg.date.date()
                print(date)
                if date not in cache:
                    await self._client.send_message(from_chat, reply)
                cache.add(date)
                print(cache)
            except Exception as e:
                traceback.print_exc()
        
        cache = set()
        chat_entity, sender_entity = None, None 
        self._set_file_handler("auto_reply")
        self._logger.info(f"Auto reply start for regex: {regex}, reply: {reply}, chat: {chat}, from: {sender}")
        self._client.start()
        self._client.run_until_disconnected()
