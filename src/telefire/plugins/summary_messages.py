import jieba
from telethon import utils
from telethon.tl.functions.channels import CreateChannelRequest

from telefire.plugins.base import Telegram, PluginMount


class SummaryMessages(Telegram, metaclass=PluginMount):
    command_name = 'summary_messages'

    async def _list_messages_async(self, chat, user, limit):
        if user is not None:
            user = await self._get_entity(user)
        msgs = []
        async for msg in self._client.iter_messages(chat, from_user=user):
            if msg.text:
                sender = await self._get_sender(msg)
                msgs.append(f'{sender}: {msg.text}')
                if len(msgs) >= limit:
                    break
        print('\n'.join(msgs[::-1]))

    def __call__(self, chat, user=None, limit=10):
        self._run_command(self._list_messages_async(chat, user, limit))
