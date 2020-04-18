from telethon.sync import events

from plugins.base import Telegram
from utils import get_url


class SpecialAttentionMode(Telegram):
    name = 'special_attention_mode'

    def action(self, event, key, *people):
        @self._client.on(events.NewMessage)
        async def _inner(evt):
            msg = evt.message
            sender = evt.message.sender
            if sender and any(sender.id==p or sender.username==p for p in people):
                channel = await self._client.get_entity(msg.to_id)
                header = "{}在{}说了: ".format(' '.join([msg.sender.first_name, msg.sender.last_name]),channel.title)
                body = evt.raw_text[:20] + ('...' if len(evt.raw_text) > 20 else '')
                url = get_url(channel, msg)
                await self._send_to_ifttt_async(event, key, header, body, url)

        self._set_file_handler('special_attention_mode')
        self._logger.info("Sending messages to IFTTT for people:{}".format(people))
        self._client.start()
        self._client.run_until_disconnected()
