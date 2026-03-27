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

from telefire.plugins.base import PluginMount
from telefire.telegram import TelegramCommand
from telefire.plugins.yvlu import yv_lu_process_image
from telefire.storage import Storage


class ChatToRedis(TelegramCommand, metaclass=PluginMount):
    command_name = "chat_to_redis"
    ignore_senders = set()


    def __call__(self, db=None):
        self.set_file_handler("chat_to_redis")
        self.chats = defaultdict(list)
        self.store = Storage(db)

        async def setup():
            await self.store.connect()

            @self.client.on(events.NewMessage())
            async def _inner(evt):
                try:
                    msg = evt.message
                    to_chat = await evt.get_chat()
                    if msg.text:
                        monitoring_chats = await self.store.smembers('chat_to_redis')
                        ignore_senders = await self.store.smembers('chat_to_redis_ignore_senders')
                        if any(self.helpers.entities.same(to_chat, chat) for chat in monitoring_chats):
                            sender = await msg.get_sender()
                            payload = {}
                            if sender:
                                if sender.username in (await self.store.smembers('bridge_bots')):
                                    payload[f'{msg.id}'] = ''.join(msg.text.split(':')[1:])
                                    payload['sender'] = msg.text.split(':')[0].strip('*')
                                elif sender.username and sender.username.lower()[-3:] == 'bot':
                                    return
                                elif sender.username not in ignore_senders and str(sender.id) not in ignore_senders:
                                    payload[f'{msg.id}'] = msg.text
                                    payload['sender'] = utils.get_display_name(sender)
                                payload['sender_id'] = sender.id
                            elif msg.post_author:
                                payload[f'{msg.id}'] = msg.text
                                payload['sender'] = msg.post_author
                            elif msg.peer_id:
                                payload[f'{msg.id}'] = msg.text
                                payload['sender'] = utils.get_display_name(msg.peer_id)
                            else:
                                self.logger.warning(f'No sender for message {msg.id} in chat {to_chat.id}, msg: {msg}')

                            if payload:
                                await self.store.xadd(f'chat_to_redis:{to_chat.id}', payload)
                                self.logger.info(f'Added message {msg.id} from {utils.get_display_name(sender)} to chat {utils.get_display_name(to_chat)}({to_chat.id})')
                except:
                    self.logger.error(traceback.format_exc())
                    self.logger.error(evt)
                    self.logger.error(msg)

        self.run_forever(setup=setup)
