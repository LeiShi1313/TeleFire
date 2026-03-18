import jieba
from dateutil import parser
from telethon import utils
from telethon.tl.functions.channels import CreateChannelRequest

from telefire.plugins.base import Telegram, PluginMount


class ListMessages(Telegram, metaclass=PluginMount):
    command_name = 'list_messages'

    async def _list_messages_async(self, chat, user, output, print_stat=False, cut=False, before=None, after=None):
        channel = await self._client.get_entity(chat)
        if user is not None:
            try:
                user = await self._client.get_entity(user)
            except ValueError as e:
                if user.isdecimal():
                    user = await self._client.get_entity(int(user))
                else:
                    raise e

        offset_date = parser.parse(before) if before else None
        min_date = parser.parse(after) if after else None

        if output == 'channel':
            result = await self._client(CreateChannelRequest(
                "List Messages",
                "Messages For {} in {}".format(utils.get_display_name(user), channel.title)))
            created_channel = result.chats[0]
            self._logger.info("Channel: {} created.".format(created_channel.title))
            await self._iter_messages_async(channel, user, '', created_channel, print_stat, lambda s: ' '.join(jieba.cut(s)) if cut else None, offset_date=offset_date, min_date=min_date)
        else:
            self._set_file_handler('list_messages', channel, user)

            self._logger.debug(channel)
            self._logger.debug(user)
            await self._iter_messages_async(channel, user, '', 'log', print_stat, offset_date=offset_date, min_date=min_date)
        self._logger.info("Listing all messages {}in {}".format(
            'for {} '.format(utils.get_display_name(user)) if user else '', channel.title))

    def __call__(self, chat, user=None, output='log', print_stat=False, cut=False, before=None, after=None):
        with self._client:
            self._client.loop.run_until_complete(
                    self._list_messages_async(chat, user, output, print_stat, jieba, before, after))
