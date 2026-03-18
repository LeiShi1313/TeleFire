import re
import traceback
from collections import defaultdict
from telethon.sync import events
from plugins.base import Telegram, PluginMount
from telethon import utils


class GetAllChats(Telegram, metaclass=PluginMount):
    command_name = 'get_lottery_winners'

    def __call__(self, chat):

        async def _get_v2_result() -> defaultdict(int):
            d = defaultdict(int)
            async for msg in self._client.iter_messages(chat, from_user='tgLotteryBot'):
                m = re.findall(r'- ([0-9]+) 获得了 \".*?\"', msg.message)
                self._logger.info(len(d))
                if m:
                    for u in m:
                        d[u] += 1
            return d

        async def _get_v1_result() -> defaultdict(int):
            d = defaultdict(int)
            async for msg in self._client.iter_messages(chat, from_user='tglottery_bot'):
                m = re.findall(r'\@[\w_]+ \(([0-9]+)\)', msg.message)
                self._logger.info(len(d))
                if m:
                    for u in m:
                        d[u] += 1
            return d
                

        @self._client.on(events.NewMessage(outgoing=True, pattern=r'\/get_(v1|v2|all)_stat'))
        async def _inner(evt):
            msg = evt.message
            try:
                to_chat = await evt.get_chat()

                # if chat is not None and to_chat.username != chat and to_chat.id != chat:
                #     return
                print(f"received {msg.text}")
                d = defaultdict(int)
                if msg.text == '/get_v1_stat' or msg.text == '/get_all_stat':
                    dd = await _get_v1_result()
                    for uid, times in dd.items():
                        d[uid] += times
                if msg.text == '/get_v2_stat' or msg.text == '/get_all_stat':
                    dd = await _get_v2_result()
                    for uid, times in dd.items():
                        d[uid] += times

                s = f'{utils.get_display_name(to_chat)}抽奖Bot{"V1" if msg.text == "/get_v1_stat" else ("V2" if msg.text == "/get_v2_stat" else "V1+V2")}统计结果:\n'
                count = 1
                for uid, times in dict(sorted(d.items(), key=lambda item: item[1], reverse=True)).items():
                    if times <= 1:
                        continue
                    user = await self._get_entity(uid)
                    if not user.username:
                        s += f"{count}: [{utils.get_display_name(user)}](tg://user?id={user.id}): {times}\n"
                    else:
                    # print(user)
                        s += f"{count}: [{utils.get_display_name(user)}](t.me/{user.username}): {times}\n"
                    count += 1
                # s += '\n（1次或以下不计入统计）'
                await self._client.send_message(to_chat, s)
            except Exception as e:
                traceback.print_exc()
        
        
        self._set_file_handler("get_lottery_wineers")
        self._logger.info(f"get_lottery_wineers start for chat: {chat}")
        self._client.start()
        self._client.run_until_disconnected()