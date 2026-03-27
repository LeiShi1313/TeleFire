from telethon import utils

from telefire.plugins.base import PluginMount
from telefire.telegram import TelegramCommand


class FindUser(TelegramCommand, metaclass=PluginMount):
    command_name = 'find_user'

    async def _find_user_async(self, chat, name, limit):
        chat_entity = await self._client.get_entity(chat)
        found = set()
        async for msg in self._client.iter_messages(chat_entity, limit=limit):
            if msg.sender and hasattr(msg.sender, 'first_name'):
                display = (msg.sender.first_name or '') + (msg.sender.last_name or '')
                if name.lower() in display.lower() and msg.sender_id not in found:
                    found.add(msg.sender_id)
                    print(f"ID: {msg.sender_id}, Name: {display}, Username: {msg.sender.username}")
        if not found:
            print(f"No user matching '{name}' found in last {limit} messages")

    def __call__(self, chat, name, limit=500):
        self.run_telegram(self._find_user_async(chat, name, limit))
