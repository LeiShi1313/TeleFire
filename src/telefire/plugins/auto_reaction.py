import os
import pickle
import random
import traceback
from collections import defaultdict, deque
from telethon import utils as telethon_utils
from telethon.sync import events
from telefire.plugins.base import PluginMount
from telefire.telegram import TelegramCommand
from telethon.tl import functions, types
from telefire.utils import get_url


class Action(TelegramCommand, metaclass=PluginMount):
    command_name = "auto_reaction"

    def __call__(self, chat, user):
        self.pre = None

        def setup():
            @self.client.on(events.NewMessage(chats=[chat], from_users=[user]))
            async def _inner(evt):
                msg = evt.message
                try:
                    # if self.pre is not None and msg.text == self.pre.text and msg.from_id != self.pre.from_id:
                    #     await self.client.send_message(msg.to_id, f"{msg.text}")
                    # self.pre = msg
                    await self.client(
                        functions.messages.SendReactionRequest(
                            peer=msg.peer_id,
                            msg_id=msg.id,
                            reaction=[types.ReactionEmoji(emoticon="🤡")],
                            add_to_recent=True,
                        )
                    )
                except Exception:
                    traceback.print_exc()

        self.set_file_handler("auto_reaction")
        self.logger.info(f"Auto reaction for chat: {chat}")
        self.run_forever(setup=setup)
