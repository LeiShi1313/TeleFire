import os
import pickle
import random
import traceback
from collections import defaultdict
from telethon import utils as telethon_utils
from telethon.sync import events
from plugins.base import Telegram, PluginMount
from utils import get_url

chats = ["gua_mei_debug"]
words = [
    "邀请码来一个",
    "邀请码走一个",
    "来个邀请码",
    "来一个邀请码",
    "邀请码给个",
    "有邀请吗",
    "有邀请码吗",
    "有人邀请吗",
    "给个邀请码",
    "邀请下",
    "有人邀请码",
    "邀请有吗",
    "邀请码多少",
    "求个邀请码",
    "求邀请码",
    "给个邀请码",
    "有没有邀请码",
]
replies = [
    "邀请码：`AFHj`",
    "邀请：`AFHj`",
    "https://www.clashcloud.net/auth/register?code=AFHj",
    "可以用我的：AFHj" "邀请链接：https://www.clashcloud.net/auth/register?code=AFHj",
    "可以用我的：`AFHj`",
    "直接用：`AFHj`",
    "[点击邀请注册](https://www.clashcloud.net/auth/register?code=AFHj)",
]
funny_replies = ["别搞我", "大哥别发了，不会回的", "没用没用没用没用", "没有滚蛋"]
blocklist = ['qaqwsw']


class Action(Telegram, metaclass=PluginMount):
    command_name = "auto_invite"
    prefix = "auto_invite_"

    async def load_vals(self):
        await self.redis.sadd(f'{self.prefix}chats', chats[0], *chats)
        await self.redis.sadd(f'{self.prefix}words', words[0], *words)
        await self.redis.sadd(f'{self.prefix}replies', replies[0], *replies)
        await self.redis.sadd(f'{self.prefix}funny_replies', funny_replies[0], *funny_replies)
        await self.redis.sadd(f'{self.prefix}blocklist', blocklist[0], *blocklist)

    async def is_blocked(self, sender):
        if sender.id and await self.redis.sismember(f'{self.prefix}blocklist', sender.id):
            return True
        elif sender.id and await self.redis.sismember(f'{self.prefix}blocklist', str(sender.id)):
            return True
        elif sender.username and await self.redis.sismember(f'{self.prefix}blocklist', sender.username):
            return True
        return False

    async def incr_attempts(self, sender):
        return await self.redis.hincrby(f'{self.prefix}attempts', sender.id)

    async def get_one_reply(self, key):
        return (await self.redis.srandmember(f'{self.prefix}{key}', 1, encoding='utf-8'))[0]

    async def is_asking_code(self, event):
        msg = event.message
        if not msg.is_reply and any([val.decode('utf-8') in event.raw_text.lower() async for val in self.redis.isscan(f'{self.prefix}words')]):
            to_chat = await event.get_chat()
            if (to_chat.username and await self.redis.sismember(f'{self.prefix}chats', to_chat.username)) \
              or (to_chat.id and await self.redis.sismember(f'{self.prefix}chats', to_chat.id)):
                sender = await event.get_sender()
                self._log_message(msg, to_chat, sender)
                return True, sender
        return False, None

    def __call__(self, redis):
        import aioredis
        @self._client.on(events.NewMessage)
        async def _inner(evt):
            msg = evt.message
            try:
                is_asking_code, sender = await self.is_asking_code(evt)
                if is_asking_code:
                    attempts = await self.incr_attempts(sender)
                    if not await self.is_blocked(sender):
                        if attempts == 1:
                            reply = await self.get_one_reply('replies')
                            await msg.reply(reply)
                        elif attempts == 3:
                            reply = await self.get_one_reply('funny_replies')
                            await msg.reply(reply)
                    else:
                        self._logger.info(f'{telethon_utils.get_display_name(sender)} in blocklist sent {msg.text}')
                    if attempts >= 3:
                        await self.redis.sadd(f'{self.prefix}blocklist', sender.id)
            except Exception as e:
                traceback.print_exc()
        
        async def connect_redis():
            self.redis = await aioredis.create_redis_pool(f'redis://{redis}')
            await self.load_vals()
        
        with self._client:
            self._client.loop.run_until_complete(connect_redis())
        self._set_file_handler("auto_invite")
        self._logger.info("Auto invite start")
        self._client.start()
        self._client.run_until_disconnected()
