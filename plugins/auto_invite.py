import random
import traceback
from collections import defaultdict
from telethon import utils as telethon_utils
from telethon.sync import events
from plugins.base import Telegram, PluginMount
from utils import get_url

chats = ['ssrcloud', 'gua_mei_debug']

words = '邀请码来一个 邀请码走一个 来个邀请码 来一个邀请码 邀请码给个 有邀请吗 有邀请码吗 有人邀请吗 给个邀请码 邀请下 有人邀请码 邀请有吗 邀请码多少 求个邀请码'

replies = [
    '邀请码：`AFHj`',
    '邀请：`AFHj`',
    'https://www.clashcloud.net/auth/register?code=AFHj',
    '可以用我的：AFHj'
    '邀请链接：https://www.clashcloud.net/auth/register?code=AFHj',
    '可以用我的：`AFHj`',
    '直接用：`AFHj`',
    '[点击邀请注册](https://www.clashcloud.net/auth/register?code=AFHj)',
]
funny_replies = [
    '别搞我',
    '大哥别发了，不会回的',
    '没用没用没用没用',
    '没有滚蛋',
    '無駄無駄無駄無駄無駄無駄無駄'
]

blocklist = [
    '1031527854',
    'xiaotianshi233',
    'HolyHigh666',
]

class Action(Telegram, metaclass=PluginMount):
    command_name = 'auto_invite'

    def __call__(self):
        attempts = defaultdict(int)
        @self._client.on(events.NewMessage)
        async def _inner(evt):
            msg = evt.message
            try:
                if not msg.is_reply and any(w.lower() in evt.raw_text.lower() for w in words.split(' ')):
                    to_chat = await evt.get_chat()
                    if any(chat == to_chat.username or chat == to_chat.id for chat in chats):
                        sender = await evt.get_sender()
                        attempts[sender.id] += 1
                        self._log_message(msg, to_chat, sender)
                        if not any(sender.id == blocked or sender.username == blocked for blocked in blocklist):
                            if attempts[sender.id] == 1:
                                await msg.reply(random.choice(replies))
                            elif attempts[sender.id] == 3:
                                await msg.reply(random.choice(funny_replies))
                        else:
                            self._logger.info(f'{telethon_utils.get_display_name(sender)} in blocklist sent {msg.text}')
            except Exception as e:
                traceback.print_stack()

                    

        self._set_file_handler('auto_invite')
        self._logger.info("Auto invite start")
        self._client.start()
        self._client.run_until_disconnected()
