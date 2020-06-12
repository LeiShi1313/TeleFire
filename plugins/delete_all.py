from telethon import utils
from plugins.base import Telegram, PluginMount


class DeleteAll(Telegram, metaclass=PluginMount):
    command_name = 'delete_all'

    async def _delete_all_async(self, chat: str, query: str) -> None:
        user = await self._client.get_me()
        channel = await self._client.get_entity(chat)

        self._set_file_handler('delete_all', channel, user)
        self._logger.info("Deleting all messages for {} in {}".format(
            utils.get_display_name(user), channel.title))
        async for msg in self._client.iter_messages(channel, from_user=user):
            if not query or (msg.text and query in msg.text):
                self._log_message(msg, channel, user)
                await msg.delete()

    def __call__(self, chat: str, query='') -> None:
        with self._client:
            self._client.loop.run_until_complete(self._delete_all_async(chat, query))
