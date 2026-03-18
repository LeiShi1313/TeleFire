import re
import traceback
from collections import defaultdict

import aioredis
from telethon import utils
from telethon.sync import events
from plugins.base import Telegram, PluginMount


class ChatStat(Telegram, metaclass=PluginMount):
    command_name = 'get_lottery_words'

    def __call__(self, redis, chat):

        async def get_lottery_words():
            try:
                self.redis = aioredis.from_url(f'redis://{redis}')

                v1_count = 0
                async for msg in self._client.iter_messages(chat, from_user='tglottery_bot'):
                    m = re.search(r'抽奖已经创建:.*群内发送关键词 (.*?) 参与抽奖', msg.message, re.DOTALL)
                    if m:
                        self._logger.info(m.group(1))
                        await self.redis.sadd(f'{chat}_lottery_words', m.group(1))
                        v1_count += 1
                self._logger.info(f"")
                v2_count = 0
                async for msg in self._client.iter_messages(chat, from_user='tgLotteryBot'):
                    m = re.search(r'抽奖 ID:.*在群内发送 (.*?) 参与抽奖', msg.message, re.DOTALL)
                    if m:
                        self._logger.info(m.group(1))
                        await self.redis.sadd(f'{chat}_lottery_words', m.group(1))
                        v2_count += 1
                self._logger.info(f"V1 lottery words: {v1_count}, V2 lottery words: {v2_count}")
            except Exception as e:
                traceback.print_exc()
        
        
        self._set_file_handler("get_lottery_words")
        self._logger.info(f"get_lottery_words start for chat: {chat}")
        with self._client:
            self._client.loop.run_until_complete(get_lottery_words())