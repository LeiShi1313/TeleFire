import traceback
from datetime import timezone
from dateutil import parser
from telethon import utils
from plugins.base import Telegram, PluginMount


class DeleteAll(Telegram, metaclass=PluginMount):
    command_name = 'delete_all'

    async def _delete_all_async(self, chat: str, before: str, query: str) -> None:
        user = await self._client.get_me()
        channel = await self._client.get_entity(chat)

        self._set_file_handler('delete_all', channel, user)
        self._logger.info("Deleting messages for {} in {}".format(
            utils.get_display_name(user), channel.title))
        async for msg in self._client.iter_messages(channel, from_user=user):
            should_delete = True
            if before is not None:
                try:
                    before_date = parser.parse(str(before)).replace(tzinfo=timezone.utc)
                    should_delete = msg.date <= before_date
                except Exception as e:
                    self._logger.warning(traceback.format_exc())
            elif query is not None:
                should_delete = msg.text and query in msg.text
            if should_delete:
                self._log_message(msg, channel, user)
                await msg.delete()
                    

    def __call__(self, chat: str, before=None, query=None) -> None:
        with self._client:
            self._client.loop.run_until_complete(self._delete_all_async(chat, before, query))
