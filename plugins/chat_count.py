import re
import math
import traceback
from collections import defaultdict
from datetime import datetime, timedelta, timezone

import aioredis
from telethon import utils
from telethon.sync import events

from plugins.base import Telegram, PluginMount

class ChatCount(Telegram, metaclass=PluginMount):
    command_name = 'chat_count'

    def __call__(self, redis, chat, limit=10):

        @self._client.on(events.NewMessage(outgoing=True))
        async def _inner(evt):
            try:
                msg = evt.message
                to_chat = await evt.get_chat()
                self.redis = aioredis.from_url(f'redis://{redis}', decode_responses=True)

                # if chat is not None and to_chat.username != chat and to_chat.id != chat:
                #     return
                m = re.search(r'\/chat_stat[ ]*(\d*)', msg.text)
                if not m:
                    return
                self._logger.info(f"received {msg.text}, {m.groups()}")
                if m.groups()[0]:
                    end = datetime.now(timezone.utc) - timedelta(days=int(m.groups()[0]))
                else:
                    end = datetime.now(timezone.utc) - timedelta(days=1)
                self._logger.info(f"Counting stat for chats start from {end}")
                d = defaultdict(int)
                ll = defaultdict(set)
                l = defaultdict(int)
                count = 0
                lottery_words = set(await self.redis.smembers(f'{chat}_lottery_words'))
                async for msg in self._client.iter_messages(chat):
                    if msg.date <= end: 
                        break
                    d[msg.from_id] += 1
                    if not msg.text:
                        continue
                    if msg.text in lottery_words:
                        if msg.text not in ll[msg.from_id]:
                            l[msg.from_id] += 1
                        ll[msg.from_id].add(msg.text)
                    count += 1
                    if count >= 1000:
                        p = math.floor(math.log(count, 10))
                        if count % int(math.pow(10, p)) == 0 and count // 1000:
                            print(count)
                count = 0
                s = f'截止{end.strftime("%Y/%m/%d, %H:%M:%S")}\n{utils.get_display_name(to_chat)}前{limit}水逼统计结果:\n'
                for uid, times in dict(sorted(d.items(), key=lambda item: item[1], reverse=True)).items():
                    if count >= limit:
                        break
                    user = await self._get_entity(uid)
                    name = utils.get_display_name(user)
                    if not user.username:
                        s += f"{count+1}: [{name[:10]}{'...' if len(name) > 10 else ''}](tg://user?id={user.id}) 总计：{times}，抽奖：{l.get(uid, 0)}\n"
                    else:
                        s += f"{count+1}: [{name[:10]}{'...' if len(name) > 10 else ''}](t.me/{user.username}) 总计：{times}，抽奖：{l.get(uid, 0)}\n"
                    count += 1
                await self._client.send_message(to_chat, s)
            except Exception:
                traceback.print_exc()
        
        self._set_file_handler("chat_count")
        self._logger.info(f"chat_count start for chat: {chat}")
        self._client.start()
        self._client.run_until_disconnected()