from telethon.sync import events
from plugins.base import Telegram, PluginMount
from utils import get_url, send_to_pushbullet


class WordsToPushbullet(Telegram, metaclass=PluginMount):
    command_name = 'words_to_pushbullet'

    def __call__(self, token, device, *words):
        @self._client.on(events.NewMessage)
        async def _inner(evt):
            if any(w.lower() in evt.raw_text.lower() for w in words):
                msg = evt.message
                channel = await self._client.get_entity(msg.to_id)
                header = "{}在{}说了: ".format(' '.join([msg.sender.first_name, msg.sender.last_name]),channel.title)
                body = evt.raw_text[:20] + ('...' if len(evt.raw_text) > 20 else '')
                url = get_url(channel, msg)
                status, resp_text = await send_to_pushbullet(token, device, header, body, url)
                if status == 200:
                    self._logger.info("[{}] {}{}\nPushbullet status: [{}]".format(url, header, body, status, resp_text))
                else:
                    self._logger.info("[{}] {}{}\nPushbullet status: [{}] {}".format(url, header, body, status, resp_text))

        self._set_file_handler('words_to_pushbullet')
        self._logger.info("Sending messages to Pushbullet for words:{}".format(words))
        self._client.start()
        self._client.run_until_disconnected()
