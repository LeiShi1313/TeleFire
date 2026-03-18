import aioredis
from telethon import utils
from telethon.tl.functions.channels import CreateChannelRequest

from plugins.base import Telegram, PluginMount


class summary(Telegram, metaclass=PluginMount):
    command_name = 'summary'

    async def _summary_messages_async(self, redis, chat, limit):
        async with aioredis.from_url(f'redis://{redis}', encoding="utf-8", decode_responses=True) as conn:
            msgs = []
            stream = await conn.xrevrange(f'chat_to_redis:{chat}', count=limit)
            for msg in stream:
                msgs.append(f'{msg[1].get("sender", "")}: {next(iter(msg[1].values()))}')
            print('\n'.join(msgs[::-1]))


    def __call__(self, redis, chat, limit=30):
        with self._client:
            self._client.loop.run_until_complete(
                    self._summary_messages_async(redis, chat, limit))
