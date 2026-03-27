import re
import pickle
import traceback
from telethon import utils, events, Button
from telethon.tl.types import TypeMessagesFilter
from telethon.tl.functions.channels import CreateChannelRequest

from telefire.plugins.base import PluginMount
from telefire.telegram import TelegramCommand


class Action(TelegramCommand, metaclass=PluginMount):
    command_name = "jarryxiaobot"

    def __call__(self):
        def setup():
            @self.client.on(events.InlineQuery)
            async def _inner(event: events.InlineQuery.Event):
                print(event)
                builder = event.builder

                await event.answer([
                    builder.article('Google search', text=event.text, buttons=[
                        [Button.url(f'Google {event.text}', f'https://www.google.com/search?q={event.text}')]
                    ])
                ])

        self.set_file_handler("jarryxiaobot")
        self.logger.info("jarryxiaobot start")
        self.run_forever(setup=setup)
