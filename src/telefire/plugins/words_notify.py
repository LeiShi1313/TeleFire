from telethon.sync import events
from telefire.plugins.base import Telegram, PluginMount
from telefire.utils import get_url


class WordsNotify(Telegram, metaclass=PluginMount):
    command_name = 'words_notify'

    def __call__(self, chats, *words):
        @self._client.on(events.NewMessage(chats=chats))
        async def _inner(evt):
            if any(w.lower() in evt.raw_text.lower() for w in words):
                msg = evt.message
                channel = await self._client.get_entity(msg.to_id)
                header = "{}在{}说了: ".format(' '.join([msg.sender.first_name, msg.sender.last_name]),channel.title)
                body = evt.raw_text[:20] + ('...' if len(evt.raw_text) > 20 else '')
                url = get_url(channel, msg)
                await self._client.send_message('gua_mei_debug', f"""**{header}**\n{body}\n{url}""")

        self._set_file_handler('words_notify')
        self._logger.info("Sending messages to chats {} for words:{}".format(chats, words))
        self._run_forever_command()
