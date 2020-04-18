from telethon.sync import events
from plugins.base import Telegram
from utils import get_url


class WordsToIfttt(Telegram):
    name = 'words_to_ifttt'

    def action(self, event, key, *words):
        @self._client.on(events.NewMessage)
        async def _inner(evt):
            if any(w.lower() in evt.raw_text.lower() for w in words):
                msg = evt.message
                channel = await self._client.get_entity(msg.to_id)
                header = "{}在{}说了: ".format(' '.join([msg.sender.first_name, msg.sender.last_name]),channel.title)
                body = evt.raw_text[:20] + ('...' if len(evt.raw_text) > 20 else '')
                url = get_url(channel, msg)
                await self._send_to_ifttt_async(event, key, header, body, url)

        self._set_file_handler('words_to_ifttt')
        self._logger.info("Sending messages to IFTTT for words:{}".format(words))
        self._client.start()
        self._client.run_until_disconnected()
