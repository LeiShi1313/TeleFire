from telethon.sync import events
from telefire.integrations import send_ifttt_event
from telefire.plugins.base import PluginMount
from telefire.telegram import TelegramCommand
from telefire.utils import get_url


class WordsToIfttt(TelegramCommand, metaclass=PluginMount):
    command_name = "words_to_ifttt"

    def __call__(self, event, key, *words):
        def setup():
            @self.client.on(events.NewMessage)
            async def _inner(evt):
                if any(w.lower() in evt.raw_text.lower() for w in words):
                    msg = evt.message
                    channel = await self.client.get_entity(msg.to_id)
                    header = "{}在{}说了: ".format(' '.join([msg.sender.first_name, msg.sender.last_name]),channel.title)
                    body = evt.raw_text[:20] + ('...' if len(evt.raw_text) > 20 else '')
                    url = get_url(channel, msg)
                    await send_ifttt_event(event, key, header, body, url, logger=self.logger)

        self.set_file_handler("words_to_ifttt")
        self.logger.info("Sending messages to IFTTT for words:{}".format(words))
        self.run_forever(setup=setup)
