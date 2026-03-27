import re
import asyncio
import traceback
from uuid import uuid4
from os.path import exists
from datetime import timedelta, datetime, timezone
from collections import defaultdict

from telethon import utils
from telethon.sync import events
from telethon.tl.functions.channels import CreateChannelRequest

from telefire.plugins.base import Telegram, PluginMount
from telefire.plugins.yvlu import yv_lu_process_image
from telefire.storage import Storage


class SummaryMan(Telegram, metaclass=PluginMount):
    command_name = 'summary_man'


    def __call__(self, chats, db=None, new_thread_seconds=120):
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
                    last_msg = await self.store.xrevrange(f'{thread_id}', count=1)
                    last_msg_time = datetime.utcfromtimestamp(int(last_msg[0][0].split('-')[0]) // 1000 if last_msg else 0).replace(tzinfo=timezone.utc) if last_msg else datetime.min.replace(tzinfo=timezone.utc)
                    msg_time = msg.date.replace(tzinfo=timezone.utc)
                    if not last_msg or msg_time - last_msg_time <= timedelta(seconds=new_thread_seconds):
                        await self.store.xadd(f'{thread_id}', {f'{msg.id}': msg.text, 'sender': utils.get_display_name(sender)})
                        self._logger.info(f'Added message {msg.id} from {utils.get_display_name(sender)} to thread {thread_id}')
                    else:
                        self.chats[to_chat.id].append(uuid4().hex)
                        new_thread_id = f"{to_chat.id}-{self.chats[to_chat.id][-1]}"
                        await self.store.xadd(f'{new_thread_id}', {f'{msg.id}': msg.text, 'sender': utils.get_display_name(sender)})
                        self._logger.info(f'Added message {msg.id} from {utils.get_display_name(sender)} to thread {new_thread_id}')
                        prev_threads = await self.store.xrange(f'{thread_id}')
                        print(prev_threads)
            except:
                traceback.print_exc()


        self.store = Storage(db)

        async def setup():
            self.me = await self._client.get_me()
            await self.store.connect()

        self._set_file_handler('summary_man')
        self.chats = defaultdict(list)
        self._run_forever_command(setup=setup)
