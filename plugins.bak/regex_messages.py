import re
import pickle
import traceback
from telethon import utils
from telethon.tl.functions.channels import CreateChannelRequest
from telethon.tl.types import TypeMessagesFilter

from plugins.base import Telegram, PluginMount

with open('codes.pkl', 'rb') as f:
    codes = pickle.load(f)
not_codes = {'vip5', 'Iplc', 'Info', 'Wifi', 'Haha'}

class Action(Telegram, metaclass=PluginMount):
    command_name = 'regex_messages'

    def _not_code(self, code):
        return all('a' <= c <= 'z' for c in code) or all('A' <= c <= 'Z' for c in code) or all('0' <= c <= '9' for c in code)

    async def _search_messages_by_regrex_async(self, chat, regex):
        import aioredis
        redis = await aioredis.create_redis_pool('redis://192.168.1.41')
        _chat = await self._client.get_entity(chat)
        regex = re.compile(r'^([a-zA-Z0-9]{4})$|邀请码.*([a-zA-Z0-9]{4})|code=([a-zA-Z0-9]{4})')
        async for msg in self._client.iter_messages(_chat):
            try:
                m = regex.match(msg.text)
                if m:
                    for code in m.groups():
                        if code and not self._not_code(code):
                            print(code)
                            await redis.hincrby('auto_invite_codes', code)
            except Exception as e:
                traceback.print_exc()

    def __call__(self, chat, regex):
        with self._client:
            self._client.loop.run_until_complete(
                    self._search_messages_by_regrex_async(chat, regex))
