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


class ChatToRedis(Telegram, metaclass=PluginMount):
    command_name = 'chat_to_redis'
    ignore_senders = set()


    def __call__(self, redis):
        @self._client.on(events.NewMessage())
        async def _inner(evt):
            try:
                msg = evt.message
                to_chat = await evt.get_chat()
                if msg.text:
                    async with aioredis.from_url(f'redis://{redis}', encoding="utf-8", decode_responses=True) as conn:
                        monitoring_chats = await conn.smembers('chat_to_redis')
                        ignore_senders = await conn.smembers('chat_to_redis_ignore_senders')
                        if any([self._is_same_entity(to_chat, chat) for chat in monitoring_chats]):
                            sender = await msg.get_sender() 
                            payload = {}
                            if sender:
                                if sender.username in (await conn.smembers('bridge_bots')):
                                    payload[f'{msg.id}'] = ''.join(msg.text.split(':')[1:])
                                    payload['sender'] = msg.text.split(':')[0].strip('*')
                                elif sender.username and sender.username.lower()[-3:] == 'bot':
                                    return
                                elif sender.username not in ignore_senders and sender.id not in ignore_senders:
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
                                self._logger.warning(f'No sender for message {msg.id} in chat {to_chat.id}, msg: {msg}')

                            if payload:
                                await conn.xadd(f'chat_to_redis:{to_chat.id}', payload)
                                self._logger.info(f'Added message {msg.id} from {utils.get_display_name(sender)} to chat {utils.get_display_name(to_chat)}({to_chat.id})')
            except:
                self._logger.error(traceback.format_exc())
                self._logger.error(evt)
                self._logger.error(msg)
                
        self._set_file_handler('chat_to_redis')
        self.chats = defaultdict(list)
        self._client.start()
        self._client.run_until_disconnected()