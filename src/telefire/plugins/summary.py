from telethon import utils
from telethon.tl.functions.channels import CreateChannelRequest

from telefire.plugins.base import PluginMount
from telefire.telegram import TelegramCommand
from telefire.storage import Storage


class summary(TelegramCommand, metaclass=PluginMount):
    command_name = "summary"

    async def _summary_messages_async(self, db, chat, limit):
        async with Storage(db) as store:
            msgs = []
            stream = await store.xrevrange(f'chat_to_redis:{chat}', count=limit)
            for msg in stream:
                msgs.append(f'{msg[1].get("sender", "")}: {next(iter(msg[1].values()))}')
            print('\n'.join(msgs[::-1]))


    def __call__(self, db=None, chat=None, limit=30):
        self.run_once(lambda: self._summary_messages_async(db, chat, limit))
