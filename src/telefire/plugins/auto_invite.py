import re
import pickle
import random
import traceback
from collections import defaultdict
from telethon import utils as telethon_utils
from telethon.sync import events
from telefire.plugins.base import PluginMount
from telefire.telegram import TelegramCommand
from telefire.utils import get_url
from telefire.storage import Storage


class Action(TelegramCommand, metaclass=PluginMount):
    command_name = "auto_invite"
    prefix = "auto_invite_"

    async def incr_attempts(self, sender):
        return await self.store.hincrby(f'{self.prefix}attempts', sender.id)

    async def get_one_reply(self, key):
        return (await self.store.srandmember(f'{self.prefix}{key}', 1))[0]

    async def get_reply(self):
        codes = await self.store.hgetall(f'{self.prefix}codes')
        codes = {k: sum(int(_v) for _v in codes.values()) - int(v) for k,v in codes.items()}
        return "也可以从群友的这些码里选择：\n{}".format(', '.join(
            map(lambda c: f'`{c}`', random.choices(list(codes.keys()), list(codes.values()), k=5))))

    async def not_code(self, code):
        return all('a' <= c <= 'z' for c in code) or all('A' <= c <= 'Z' for c in code) or all('0' <= c <= '9' for c in code) or await self.store.sismember(f'{self.prefix}not_codes', code)

    async def is_asking_code(self, msg, to_chat, sender):
        if not msg.is_reply and msg.text and any([val in msg.text.lower() async for val in self.store.isscan(f'{self.prefix}words')]):
            if (to_chat.username and await self.store.sismember(f'{self.prefix}chats', to_chat.username)) \
              or (to_chat.id and await self.store.sismember(f'{self.prefix}chats', to_chat.id)):
                self.helpers.messages.log(msg, to_chat, sender)
                return True
        return False

    async def is_in_chat(self, to_chat):
        return (to_chat.username and await self.store.sismember(f'{self.prefix}chats', to_chat.username)) \
              or (to_chat.id and await self.store.sismember(f'{self.prefix}chats', to_chat.id))

    def __call__(self, db=None, prefix=None):
        if prefix is not None:
            self.prefix = prefix
        regex = re.compile(r'^([a-zA-Z0-9]{4})$|^.*邀请码.*([a-zA-Z0-9]{4}).*$|^.*code=([a-zA-Z0-9]{4}).*$', re.MULTILINE)

        self.store = Storage(db)

        async def setup():
            self.me = await self.client.get_me()
            await self.store.connect()

            @self.client.on(events.NewMessage)
            async def _inner(evt):
                msg = evt.message
                try:
                    to_chat = await evt.get_chat()
                    if not await self.is_in_chat(to_chat):
                        return

                    sender = await evt.get_sender()
                    m = regex.match(msg.text)
                    if msg.text and m:
                        for code in m.groups():
                            if code and not await self.not_code(code):
                                self.helpers.messages.log(msg, to_chat, sender)
                                self.logger.info(f'{telethon_utils.get_display_name(sender)}({sender.id}) sent a code: {code}')
                                await self.incr_attempts(sender)
                                await self.store.hincrby('auto_invite_codes', code)
                        return
                    if await self.is_asking_code(msg, to_chat, sender):
                        attempts = await self.incr_attempts(sender)
                        if attempts == 1 or sender.id == self.me.id:
                            one_reply = await self.get_one_reply('replies')
                            await msg.reply(one_reply)
                        else:
                            self.logger.info(f'{telethon_utils.get_display_name(sender)}({sender.id}) in {to_chat.username} sent {msg.text}, attempts: {attempts}')
                except Exception:
                    traceback.print_exc()

        self.set_file_handler("auto_invite")
        self.logger.info("Auto invite start")
        self.run_forever(setup=setup)
