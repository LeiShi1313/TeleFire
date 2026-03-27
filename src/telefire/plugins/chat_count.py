import re
import math
import traceback
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from telethon import utils
from telethon.sync import events

from telefire.plugins.base import PluginMount
from telefire.telegram import TelegramCommand
from telefire.storage import Storage

class ChatCount(TelegramCommand, metaclass=PluginMount):
    command_name = "chat_count"

    def __call__(self, chat, limit=10, db=None):
        def setup():
            @self.client.on(events.NewMessage(outgoing=True))
            async def _inner(evt):
                try:
                    msg = evt.message
                    to_chat = await evt.get_chat()

                    m = re.search(r'\/chat_stat[ ]*(\d*)', msg.text)
                    if not m:
                        return
                    self.logger.info(f"received {msg.text}, {m.groups()}")
                    if m.groups()[0]:
                        end = datetime.now(timezone.utc) - timedelta(days=int(m.groups()[0]))
                    else:
                        end = datetime.now(timezone.utc) - timedelta(days=1)
                    self.logger.info(f"Counting stat for chats start from {end}")
                    d = defaultdict(int)
                    count = 0
                    async for msg in self.client.iter_messages(chat):
                        if msg.date <= end:
                            break
                        d[msg.from_id] += 1
                        count += 1
                        if count >= 1000:
                            p = math.floor(math.log(count, 10))
                            if count % int(math.pow(10, p)) == 0 and count // 1000:
                                print(count)
                    count = 0
                    s = f'截止{end.strftime("%Y/%m/%d, %H:%M:%S")}\n{utils.get_display_name(to_chat)}前{limit}水逼统计结果:\n'
                    for uid, times in dict(sorted(d.items(), key=lambda item: item[1], reverse=True)).items():
                        if count >= limit:
                            break
                        user = await self.helpers.entities.get(uid)
                        name = utils.get_display_name(user)
                        if not user.username:
                            s += f"{count+1}: [{name[:10]}{'...' if len(name) > 10 else ''}](tg://user?id={user.id}) 总计：{times}\n"
                        else:
                            s += f"{count+1}: [{name[:10]}{'...' if len(name) > 10 else ''}](t.me/{user.username}) 总计：{times}\n"
                        count += 1
                    await self.client.send_message(to_chat, s)
                except Exception:
                    traceback.print_exc()

        self.set_file_handler("chat_count")
        self.logger.info(f"chat_count start for chat: {chat}")
        self.run_forever(setup=setup)
