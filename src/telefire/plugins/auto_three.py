import re
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
    command_name = "auto_three"

    def __call__(self, chat):
        @self._client.on(events.NewMessage(chats=[chat]))
        async def _inner(evt):
            msg = evt.message
            try:
                print(msg.text)
                if "啪啪结束" in msg.text or msg.text.startswith('/start'):
                    await self._client.send_message(chat, "/startgroupsex")
                    await asyncio.sleep(random.randint(5, 20))
                    invited_users = set([(await self._client.get_me()).id, 302624748])
                    max_people = random.randint(5, 10)
                    async for prev_msg in self._client.iter_messages(chat, max_id=msg.id-1):
                        potential_guest = await prev_msg.get_sender()
                        if potential_guest.id not in invited_users:
                            await prev_msg.reply('/invite')
                            invited_users.add(potential_guest.id)
                            await asyncio.sleep(random.randint(5, 20))
                        if len(invited_users) >= max_people:
                            break
            except Exception:
                traceback.print_exc()
        
        self._set_file_handler("auto_three")
        self._logger.info("Auto three start")
        self.run_telegram_forever()
