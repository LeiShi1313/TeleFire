import re
import asyncio
import traceback
from uuid import uuid4
from os.path import exists
from datetime import timedelta, datetime, timezone
from collections import defaultdict

import aioredis
from telethon import utils
from telethon.sync import events
from telethon.tl.functions.channels import CreateChannelRequest

from plugins.base import Telegram, PluginMount
from plugins.yvlu import yv_lu_process_image


class SummaryMan(Telegram, metaclass=PluginMount):
    command_name = 'summary_man'


    def __call__(self, chats, redis, new_thread_seconds=120):
        @self._client.on(events.NewMessage(chats=chats))
        async def _inner(evt):
            try:
                msg = evt.message
                to_chat = await evt.get_chat()
                if msg.text:
                    sender = await msg.get_sender() 
                    if not self.chats[to_chat.id]:
                        self.chats[to_chat.id].append(uuid4().hex)
                    thread_id = f"{to_chat.id}-{self.chats[to_chat.id][-1]}"
                    async with aioredis.from_url(f'redis://{redis}', encoding="utf-8", decode_responses=True) as conn:
                        last_msg = await conn.xrevrange(f'{thread_id}', count=1)
                        last_msg_time = datetime.utcfromtimestamp(int(last_msg[0][0].split('-')[0]) // 1000 if last_msg else 0).replace(tzinfo=timezone.utc)
                        msg_time = msg.date.replace(tzinfo=timezone.utc)
                        if not last_msg or msg_time - last_msg_time <= timedelta(seconds=new_thread_seconds):
                            await conn.xadd(f'{thread_id}', {f'{msg.id}': msg.text, 'sender': utils.get_display_name(sender)})
                            self._logger.info(f'Added message {msg.id} from {utils.get_display_name(sender)} to thread {thread_id}')
                        else:
                            self.chats[to_chat.id].append(uuid4().hex)
                            new_thread_id = f"{to_chat.id}-{self.chats[to_chat.id][-1]}"
                            await conn.xadd(f'{new_thread_id}', {f'{msg.id}': msg.text, 'sender': utils.get_display_name(sender)})
                            self._logger.info(f'Added message {msg.id} from {utils.get_display_name(sender)} to thread {new_thread_id}')
                            prev_threads = await conn.xrange(f'{thread_id}')
                            print(prev_threads)
            except:
                traceback.print_exc()
                

        async def prepare():
            self.me = await self._client.get_me()
        
        self._set_file_handler('summary_man')
        self.chats = defaultdict(list)
        with self._client:
            self._client.loop.run_until_complete(prepare())
        self._client.start()
        self._client.run_until_disconnected()