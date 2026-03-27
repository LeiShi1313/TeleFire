from telefire.plugins.base import PluginMount
from telefire.telegram import TelegramCommand


class SummaryMessages(TelegramCommand, metaclass=PluginMount):
    command_name = "summary_messages"

    async def _list_messages_async(self, chat, user, limit):
        if user is not None:
            user = await self.helpers.entities.get(user)
        msgs = []
        async for msg in self.client.iter_messages(chat, from_user=user):
            if msg.text:
                sender = await self.helpers.messages.sender_name(msg)
                msgs.append(f"{sender}: {msg.text}")
                if len(msgs) >= limit:
                    break
        print("\n".join(msgs[::-1]))

    def __call__(self, chat, user=None, limit=10):
        self.run_once(lambda: self._list_messages_async(chat, user, limit))
