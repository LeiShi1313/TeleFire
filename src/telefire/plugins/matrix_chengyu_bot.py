import re
import random
import asyncio
import traceback
from itertools import chain
from collections import defaultdict

from telefire.matrix import MatrixCommand
from telefire.plugins.base import PluginMount
from mautrix.types import (
    EventType,
    Filter,
    RoomFilter,
    MessageEvent,
    MessageType,
    TextMessageEventContent,
)
from telefire.util.chengyu import load_chengyu_dict


class Action(MatrixCommand, metaclass=PluginMount):
    command_name = "matrix_chengyu_bot"

    chinese_pattern = re.compile(r"^[\u4e00-\u9fff]+$")

    def __init__(self, log_level: str = "info"):
        super().__init__(log_level=log_level)
        self.chengyu = defaultdict(list)
        self.used_chengyu = defaultdict(set)
        self.last_chengyu: dict[str, str] = {}
        self.waiting_tasks: dict[str, asyncio.Task] = {}
        self.wait_time_range: tuple[int, int] = (600, 3600)

    def is_chengyu(self, text: str):
        if not self.chengyu:
            self.chengyu = load_chengyu_dict()
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
            self.used_chengyu[room_id].clear()
            candidates = self.chengyu[last_char]

        return random.choice(candidates) if candidates else None

    async def delayed_response(self, room_id, last_chengyu_text):
        try:
            wait_time = random.uniform(*self.wait_time_range)
            self._logger.info(
                f"Waiting for {wait_time:.2f} seconds before responding in room {room_id}"
            )
            await asyncio.sleep(wait_time)

            if (
                room_id not in self.waiting_tasks
                or self.waiting_tasks[room_id].cancelled()
            ):
                return

            last_char = last_chengyu_text[-1]
            next_chengyu = self.find_next_chengyu(last_char, room_id)

            self._logger.info(
                f"Last chengyu: {last_chengyu_text}, last character: {last_char}, next chengyu: {next_chengyu}"
            )
            if next_chengyu:
                await self.matrix.client.send_message(
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
            pass
        except Exception as e:
            self._logger.error(f"Error in delayed_response: {e}")
            traceback.print_exc()
        finally:
            current_task = asyncio.current_task()
            if current_task is not None and self.waiting_tasks.get(room_id) is current_task:
                del self.waiting_tasks[room_id]

    def __call__(self, chat, wait_start: int = 600, wait_end: int = 3600, dry_run: bool = False):

        def setup(matrix):
            @matrix.client.on(EventType.ROOM_MESSAGE)
            async def _inner(event: MessageEvent):
                try:
                    if event.room_id != chat:
                        return
                    if event.sender == matrix.user_id:
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
                            f"Received chengyu: {body} in room {await matrix.get_room_display_name(chat)}"
                        )

                        if event.room_id in self.waiting_tasks:
                            self.waiting_tasks[event.room_id].cancel()

                        self.last_chengyu[event.room_id] = body
                        self.used_chengyu[event.room_id].add(body)
                        task = asyncio.create_task(
                            self.delayed_response(event.room_id, body)
                        )
                        self.waiting_tasks[event.room_id] = task
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
        self.run_matrix_forever(
            setup=setup,
            filter_data=Filter(room=RoomFilter(rooms=[chat])),
        )
