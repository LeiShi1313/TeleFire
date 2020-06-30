import os
import pickle
import random
import traceback
from collections import defaultdict
from telethon import utils as telethon_utils
from telethon.sync import events
from plugins.base import Telegram, PluginMount
from utils import get_url


class Action(Telegram, metaclass=PluginMount):
    command_name = "auto_invite"

    def __init__(self):
        super().__init__()
        self.attempts = defaultdict(int)
        self.chats = ["gua_mei_debug"]
        self.words = [
            "邀请码来一个",
            "邀请码走一个",
            "来个邀请码",
            "来一个邀请码",
            "邀请码给个",
            "有邀请吗",
            "有邀请码吗",
            "有人邀请吗",
            "给个邀请码",
            "邀请下",
            "有人邀请码",
            "邀请有吗",
            "邀请码多少",
            "求个邀请码",
            "求邀请码",
            "给个邀请码",
            "有没有邀请码",
        ]
        self.replies = [
            "邀请码：`AFHj`",
            "邀请：`AFHj`",
            "https://www.clashcloud.net/auth/register?code=AFHj",
            "可以用我的：AFHj" "邀请链接：https://www.clashcloud.net/auth/register?code=AFHj",
            "可以用我的：`AFHj`",
            "直接用：`AFHj`",
            "[点击邀请注册](https://www.clashcloud.net/auth/register?code=AFHj)",
        ]
        self.funny_replies = ["别搞我", "大哥别发了，不会回的", "没用没用没用没用", "没有滚蛋"]
        self.blocklist = set()
        self.load_vals()

    def load_vals(self):
        if os.path.exists("words.pkl"):
            with open("words.pkl", "rb") as f:
                self.words += pickle.load(f)
        if os.path.exists("blocklist.pkl"):
            with open("blocklist.pkl", "rb") as f:
                self.blocklist.update(pickle.load(f))
        if os.path.exists("replies.pkl"):
            with open("replies.pkl", "rb") as f:
                self.replies += pickle.load(f)
        if os.path.exists("funny_replies.pkl"):
            with open("funny_replies.pkl", "rb") as f:
                self.funny_replies += pickle.load(f)
        if os.path.exists("attempts.pkl"):
            with open("attempts.pkl", 'rb') as f:
                self.attempts.update(pickle.load(f))

    def save_vals(self):
        with open("words.pkl", "wb") as f:
            pickle.dump(self.words, f)
        with open("blocklist.pkl", "wb") as f:
            pickle.dump(self.blocklist, f)
        with open("replies.pkl", "wb") as f:
            pickle.dump(self.replies, f)
        with open("funny_repllies.pkl", "wb") as f:
            pickle.dump(self.funny_replies, f)
        with open("attempts.pkl", "wb") as f:
            pickle.dump(self.attempts, f)
    
    def is_blocked(self, sender):
        return any(
            sender.id == blocked \
                or sender.username == blocked \
                    or str(sender.id) == blocked for blocked in self.blocklist)

    async def is_asking_code(self, event):
        msg = event.message
        if not msg.is_reply and any(w.lower() in event.raw_text.lower() for w in self.words):
            to_chat = await event.get_chat()
            if any(chat == to_chat.username or chat == to_chat.id for chat in self.chats):
                sender = await event.get_sender()
                self._log_message(msg, to_chat, sender)
                return True, sender
        return False, None

    def __call__(self):
        @self._client.on(events.NewMessage)
        async def _inner(evt):
            msg = evt.message
            try:
                is_asking_code, sender = await self.is_asking_code(evt)
                if is_asking_code:
                    self.load_vals()
                    self.attempts[sender.id] += 1
                    if not self.is_blocked(sender):
                        if self.attempts[sender.id] == 1:
                            await msg.reply(random.choice(self.replies))
                        elif self.attempts[sender.id] == 3:
                            await msg.reply(random.choice(self.funny_replies))
                    else:
                        self._logger.info(f'{telethon_utils.get_display_name(sender)} in blocklist sent {msg.text}')
                    if self.attempts[sender.id] >= 3:
                        self.blocklist.add(sender.id)
                    self.save_vals()
            except Exception as e:
                traceback.print_exc()

        self._set_file_handler("auto_invite")
        self._logger.info("Auto invite start")
        self._client.start()
        self._client.run_until_disconnected()
