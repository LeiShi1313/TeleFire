import os
import pickle
import random
import traceback
from collections import defaultdict
from telethon import utils as telethon_utils
from telethon.sync import events
from telefire.plugins.base import PluginMount
from telefire.telegram import TelegramCommand
from telefire.utils import get_url


class Action(TelegramCommand, metaclass=PluginMount):
    command_name = "auto_frds"

    def __call__(self, regex, reply, chat=None, sender=None):
        def setup():
            @self.client.on(events.NewMessage(pattern=regex))
            async def _inner(evt):
                msg = evt.message
                try:
                    from_chat = await evt.get_chat()
                    from_sender = await msg.get_sender()
                    if chat is not None and (await self.helpers.entities.get(chat)).id != from_chat.id:
                        return
                    if sender is not None and (await self.helpers.entities.get(sender)).id != from_sender.id:
                        return

                    date = msg.date.date()
                    print(date)
                    if date not in cache:
                        await self.client.send_message(from_chat, reply)
                    cache.add(date)
                    print(cache)
                except Exception:
                    traceback.print_exc()
        
        cache = set()
        chat_entity, sender_entity = None, None 
        self.set_file_handler("auto_reply")
        self.logger.info(f"Auto reply start for regex: {regex}, reply: {reply}, chat: {chat}, from: {sender}")
        self.run_forever(setup=setup)
