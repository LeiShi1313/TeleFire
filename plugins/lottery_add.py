import re
import json
import traceback
from uuid import uuid4
from collections import defaultdict

import aioredis
from telethon import utils
from telethon.sync import events
from plugins.base import Telegram, PluginMount


class ChatStat(Telegram, metaclass=PluginMount):
    command_name = 'lottery_add'

    def __call__(self, redis, chat, title, prize, limit, words, count=1):

        async def get_lottery_words():
            try:
                chat_entity = await self._get_entity(chat)
                lottery_id = str(uuid4())

                stat = {
                    'chat': chat_entity.id,
                    'title': title,
                    'words': words,
                    'limit': limit,
                    'prize': prize,
                    'count': count,
                }
                async with aioredis.from_url(f'redis://{redis}', encoding="utf-8", decode_responses=True) as conn:
                    await conn.sadd(f"{chat_entity.id}-lotterys", lottery_id)
                    await conn.hset(f"{lottery_id}-stat", mapping=stat)
                self._logger.info(f"Lottery {title} in chat {chat}, {prize}*{count} created!")

            except Exception as e:
                traceback.print_exc()
        
        
        self._set_file_handler("lottery_add")
        with self._client:
            self._client.loop.run_until_complete(get_lottery_words())