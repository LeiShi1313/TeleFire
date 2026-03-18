import re
import pickle
import traceback
from telethon import utils, events, Button
from telethon.tl.types import TypeMessagesFilter
from telethon.tl.functions.channels import CreateChannelRequest

from plugins.base import Telegram, PluginMount


class Action(Telegram, metaclass=PluginMount):
    command_name = 'jarryxiaobot'

    def __call__(self):
        @self._client.on(events.InlineQuery)
        async def _inner(event: events.InlineQuery.Event):
            print(event)
            builder = event.builder

            await event.answer([
                builder.article('Google search', text=event.text, buttons=[
                    [Button.url(f'Google {event.text}', f'https://www.google.com/search?q={event.text}')]
                ])
            ])

        self._set_file_handler("jarryxiaobot")
        self._logger.info("jarryxiaobot start")
        self._client.start()
        self._client.run_until_disconnected()
