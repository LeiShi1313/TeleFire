import re
import traceback
from collections import defaultdict

import aioredis
from telethon import utils
from telethon.sync import events
from plugins.base import Telegram, PluginMount


class ChatStat(Telegram, metaclass=PluginMount):
    command_name = 'log_lottery_words'

    def __call__(self, redis, chat):

        @self._client.on(events.NewMessage(chats=[chat]))
        async def _inner(evt):
            msg = evt.message
            try:
                sender = await msg.get_sender()
                if sender.username == 'tgLotteryBot':
                    m = re.search(r'抽奖 ID:.*在群内发送 (.*?) 参与抽奖', msg.message, re.DOTALL)
                    if m:
                        print(f"received {msg.text}")
                        await self.redis.sadd(f'{chat}_lottery_words', m.group(1))
                        self._logger.info(f"Logged lottery words: {m.group(1)}")
            except Exception as e:
                traceback.print_exc()
        
        
        self._set_file_handler("log_lottery_words")
        self._logger.info(f"log_lottery_words start for chat: {chat}")
        self.redis = aioredis.from_url(f'redis://{redis}')
        self._client.start()
        self._client.run_until_disconnected()