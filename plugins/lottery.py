import os
import json
import pickle
import random
import traceback
from collections import defaultdict

import aioredis
from telethon import utils as telethon_utils
from telethon.sync import events
from plugins.base import Telegram, PluginMount
from utils import get_url


class Action(Telegram, metaclass=PluginMount):
    command_name = "lottery"

    def __call__(self, redis, chat):

        @self._client.on(events.NewMessage(chats=[chat]))
        async def _inner(evt):
            msg = evt.message
            try:
                chat_entity = await evt.get_chat()
                sender = await evt.get_sender()
                lotterys = []
                async with aioredis.from_url(f'redis://{redis}', encoding="utf-8", decode_responses=True).pipeline(transaction=True) as pipe:
                    for lottery_id in (await pipe.immediate_execute_command("smembers", f"{chat_entity.id}-lotterys")):
                        lottery = await pipe.immediate_execute_command('hgetall', f'{lottery_id}-stat')
                        if not lottery or lottery.get('ended', False):
                            continue
                        lotterys.append(lottery)

                        attended_users = await pipe.immediate_execute_command('smembers', lottery_id)
                        if len(attended_users) < int(lottery.get('limit', 0)):
                            if msg.text == lottery.get('words'):
                                if not await pipe.immediate_execute_command('sismember', lottery_id, sender.id):
                                    self._logger.info(f"{telethon_utils.get_display_name(sender)} 参与{lottery.get('title')}成功，已经有{len(attended_users)+1}人参加")
                                    await pipe.immediate_execute_command('sadd', lottery_id, sender.id)
                                    await msg.reply("参与成功！")
                                    break
                        else:
                            self._logger.info(f"抽奖{lottery.get('title')}已经结束，已经有{len(attended_users)}人参加")
                            # 开抽
                            winner = await self._get_entity(random.choice(list(attended_users)))
                            await self._client.send_message(chat_entity, f"[{telethon_utils.get_display_name(winner)}](tg://user?id={winner.id})")
                            await pipe.immediate_execute_command('del', f'{lottery_id}-stat')
                            break
                    else:
                        if lotterys and msg.text == '怎么中奖':
                            reply = f"{telethon_utils.get_display_name(chat_entity)}当前正在进行中的抽奖：\n\n" + \
                                "\n\n".join(f"{l.get('title')}\n- {l.get('prize')} x {l.get('count')}\n在本群发送`{l.get('words')}`参与抽奖\n" for l in lotterys)
                            await msg.reply(reply)

            except Exception as e:
                traceback.print_exc()
        
        self._set_file_handler("lottery")
        self._logger.info(f"Lottery bot start for chat: {chat}")
        self._client.start()
        self._client.run_until_disconnected()
