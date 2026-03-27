from telethon.sync import events
from telefire.integrations import send_ifttt_event

from telefire.plugins.base import PluginMount
from telefire.telegram import TelegramCommand
from telefire.utils import get_url


class SpecialAttentionMode(TelegramCommand, metaclass=PluginMount):
    command_name = "special_attention_mode"

    def __call__(self, event, key, *people):
        def setup():
            @self.client.on(events.NewMessage)
            async def _inner(evt):
                msg = evt.message
                sender = evt.message.sender
                if sender and any(sender.id == p or sender.username == p for p in people):
                    channel = await self.client.get_entity(msg.to_id)
                    header = "{}在{}说了: ".format(' '.join([msg.sender.first_name, msg.sender.last_name]),channel.title)
                    body = evt.raw_text[:20] + ('...' if len(evt.raw_text) > 20 else '')
                    url = get_url(channel, msg)
                    await send_ifttt_event(event, key, header, body, url, logger=self.logger)

        self.set_file_handler("special_attention_mode")
        self.logger.info("Sending messages to IFTTT for people:{}".format(people))
        self.run_forever(setup=setup)
