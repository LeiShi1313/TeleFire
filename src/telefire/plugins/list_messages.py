import jieba
from dateutil import parser
from telethon import utils
from telethon.tl.functions.channels import CreateChannelRequest

from telefire.plugins.base import PluginMount
from telefire.telegram import TelegramCommand


class ListMessages(TelegramCommand, metaclass=PluginMount):
    command_name = "list_messages"

    async def _list_messages_async(self, chat, user, output, print_stat=False, cut=False, before=None, after=None):
        channel = await self.client.get_entity(chat)
        if user is not None:
            try:
                user = await self.client.get_entity(user)
            except ValueError as e:
                if user.isdecimal():
                    user = await self.client.get_entity(int(user))
                else:
                    raise e

        offset_date = parser.parse(before) if before else None
        min_date = parser.parse(after) if after else None

        if output == 'channel':
            result = await self.client(CreateChannelRequest(
                "List Messages",
                "Messages For {} in {}".format(utils.get_display_name(user), channel.title)))
            created_channel = result.chats[0]
            self.logger.info("Channel: {} created.".format(created_channel.title))
            await self.helpers.messages.iter(
                channel,
                user,
                "",
                created_channel,
                print_stat,
                lambda s: " ".join(jieba.cut(s)) if cut else None,
                offset_date=offset_date,
                min_date=min_date,
            )
        else:
            self.set_file_handler("list_messages", channel, user)

            self.logger.debug(channel)
            self.logger.debug(user)
            await self.helpers.messages.iter(
                channel,
                user,
                "",
                "log",
                print_stat,
                offset_date=offset_date,
                min_date=min_date,
            )
        self.logger.info("Listing all messages {}in {}".format(
            'for {} '.format(utils.get_display_name(user)) if user else '', channel.title))

    def __call__(self, chat, user=None, output='log', print_stat=False, cut=False, before=None, after=None):
        self.run_once(
            lambda: self._list_messages_async(chat, user, output, print_stat, cut, before, after)
        )
