import os
import pickle
import random
import traceback
from collections import defaultdict, deque
from telethon import utils as telethon_utils
from telethon.sync import events
from telefire.plugins.base import Telegram, PluginMount
from telethon.tl import functions, types
from telefire.utils import get_url


class Action(Telegram, metaclass=PluginMount):
    command_name = "auto_reaction"

    def __call__(self, chat, user):
        self.pre = None

        @self._client.on(events.NewMessage(chats=[chat], from_users=[user]))
        async def _inner(evt):
            msg = evt.message
            try:
                # if self.pre is not None and msg.text == self.pre.text and msg.from_id != self.pre.from_id:
                #     await self._client.send_message(msg.to_id, f"{msg.text}")
                # self.pre = msg
                await self._client(
                    functions.messages.SendReactionRequest(
                        peer=msg.peer_id,
                        msg_id=msg.id,
                        reaction=[types.ReactionEmoji(emoticon="🤡")],
                        add_to_recent=True,
                    )
                )
            except Exception as e:
                traceback.print_exc()

        self._set_file_handler("auto_reaction")
        self._logger.info(f"Auto reaction for chat: {chat}")
        self._client.start()
        self._client.run_until_disconnected()
