import os
import re
import pickle
import random
import asyncio
import traceback
from itertools import chain
from typing import Dict, Tuple
from collections import defaultdict
from telethon import utils as telethon_utils
from telethon.sync import events
from telefire.plugins.base import Matrix, PluginMount
from mautrix.types import (
    EventType,
    Filter,
    RoomFilter,
    MessageEvent,
    MessageType,
    TextMessageEventContent,
    RoomEventFilter,
    StateFilter,
)
from telefire.util.chengyu import load_chengyu_dict


class Action(Matrix, metaclass=PluginMount):
    command_name = "matrix_chengyu_bot"

    chengyu = defaultdict(list)
    used_chengyu = defaultdict(set)
    last_chengyu: Dict[str, str] = {}
    chinese_pattern = re.compile(r"^[\u4e00-\u9fff]+$")
    waiting_tasks: Dict[str, asyncio.Task] = {}
    wait_time_range: Tuple[int, int]

    def is_chengyu(self, text: str):
        if not self.chengyu:
            self.load_chengyu_dict()
        text = text.strip()

        if len(text) != 4:
            return False
        if not self.chinese_pattern.match(text):
            return False
        return True

    def find_next_chengyu(self, last_char, room_id):
        if last_char not in self.chengyu:
            return None

        candidates = [
            chengyu
            for chengyu in self.chengyu[last_char]
            if chengyu not in self.used_chengyu[room_id]
        ]

        if not candidates:
            # If all chengyu with this character are used, reset and try again
            self.used_chengyu[room_id].clear()
            candidates = self.chengyu_dict[last_char]

        return random.choice(candidates) if candidates else None

    async def delayed_response(self, room_id, last_chengyu_text):
        try:
            wait_time = random.uniform(*self.wait_time_range)
            print(f"Waiting for {wait_time:.2f} seconds before responding in room {room_id}")
            await asyncio.sleep(wait_time)

            if (
                room_id not in self.waiting_tasks
                or self.waiting_tasks[room_id].cancelled()
            ):
                return

            last_char = last_chengyu_text[-1]
            next_chengyu = self.find_next_chengyu(last_char, room_id)

            print(
                f"Last chengyu: {last_chengyu_text}, last character: {last_char}, next chengyu: {next_chengyu}"
            )
            if next_chengyu:
                await self._client.send_message(
                    room_id=room_id,
                    content=TextMessageEventContent(
                        msgtype=MessageType.TEXT, body=next_chengyu
                    ),
                )
                self.last_chengyu[room_id] = next_chengyu
                self.used_chengyu[room_id].add(next_chengyu)
            else:
                if room_id in self.last_chengyu:
                    del self.last_chengyu[room_id]
                self.used_chengyu[room_id].clear()
        except asyncio.CancelledError:
            # Task was cancelled, do nothing
            pass
        except Exception as e:
            print(f"Error in delayed_response: {e}")
            traceback.print_exc()
        finally:
            if room_id in self.waiting_tasks:
                del self.waiting_tasks[room_id]

    def __call__(self, chat, wait_start: int = 600, wait_end: int = 3600, dry_run: bool = False):
        @self._client.on(EventType.ROOM_MESSAGE)
        async def _inner(event: MessageEvent):
            try:
                if event.room_id != chat:
                    return
                if event.sender == self.user_id:
                    return
                if (
                    not hasattr(event.content, "body")
                    or not event.content.body
                    or not event.content.body.strip()
                    or len(event.content.body.strip()) < 2
                ):
                    return

                body = event.content.body.strip()
                if self.is_chengyu(body):
                    self._logger.info(
                        f"Received chengyu: {body} in room {await self.get_room_displayname(chat)}"
                    )

                    if event.room_id in self.waiting_tasks:
                        self.waiting_tasks[event.room_id].cancel()

                    self.last_chengyu[event.room_id] = body
                    self.used_chengyu[event.room_id].add(body)
                    self.waiting_tasks[event.room_id] = self.loop.create_task(
                        self.delayed_response(event.room_id, body)
                    )
                else:
                    if event.room_id in self.waiting_tasks:
                        self.waiting_tasks[event.room_id].cancel()
                        del self.waiting_tasks[event.room_id]

            except Exception as e:
                self._logger.error(f"Error in event handler: {e}")
                traceback.print_exc()
                return

        self.wait_time_range = (wait_start, wait_end)
        self.chengyu = load_chengyu_dict()
        self._logger.info(
            f"Loaded {len(set(chain.from_iterable(self.chengyu.values())))} chengyu from dictionary."
        )
        self.start(filter_data=Filter(room=RoomFilter(rooms=[chat])))
