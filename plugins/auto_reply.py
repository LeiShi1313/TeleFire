from telethon.sync import events
from plugins.base import Telegram, PluginMount
from utils import get_url


class AutoReply(Telegram, metaclass=PluginMount):
    command_name = 'auto_reply'

    def __call__(self, chat, reply, *words):
        @self._client.on(events.NewMessage)
        async def _inner(evt):
            msg = evt.message
            if not msg.is_reply and any(w.lower() in evt.raw_text.lower() for w in words):
                to_chat = await evt.get_chat()
                if to_chat.username=='gua_mei_debug' or chat == to_chat.username or chat == to_chat.id:
                    sender = await evt.get_sender()
                    self._log_message(msg, to_chat, sender)
                    await msg.reply(reply)

        self._set_file_handler('auto_reply_[{}]'.format(chat))
        self._logger.info("Auto reply in chat {} for words:{}".format(chat, words))
        self._client.start()
        self._client.run_until_disconnected()
