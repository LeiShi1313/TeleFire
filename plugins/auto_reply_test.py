import os
import pickle
import random
import traceback
from collections import defaultdict
from telethon import utils as telethon_utils
from telethon.sync import events
from plugins.base import Telegram, PluginMount
from utils import get_url


class Action(Telegram, metaclass=PluginMount):
    command_name = "auto_reply_test"

    def __call__(self, chat, reply):
        import aioredis
        @self._client.on(events.NewMessage)
        async def _inner(evt):
            msg = evt.message
            try:
                to_chat = await evt.get_chat()
                sender = await msg.get_sender()
                if not msg.is_reply and to_chat.username == chat or to_chat.id == chat:
                    self._log_message(msg, to_chat, sender)
                    await evt.reply(reply)
            except Exception as e:
                traceback.print_exc()
        
        
        self._set_file_handler("auto_reply_test")
        self._logger.info(f"Auto reply start for chat {chat}, reply: {reply}")
        self._client.start()
        self._client.run_until_disconnected()
