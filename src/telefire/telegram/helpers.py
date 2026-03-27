import logging
import re
from collections import Counter
from datetime import datetime
from math import floor
from pathlib import Path

import aiohttp
from telethon import TelegramClient, utils
from telethon.hints import EntitiesLike
from telethon.tl.types import Channel, Message, User

from telefire.utils import get_url


class TelegramLogHelper:
    def __init__(self, logger):
        self.logger = logger
        self._formatter = logging.Formatter("%(message)s")

    def set_file_handler(self, method, channel=None, user=None, query=None):
        path = Path("logs").joinpath(method)
        if channel:
            path = path.joinpath(channel.title)
        if user:
            path = path.joinpath(utils.get_display_name(user))
        path.mkdir(parents=True, exist_ok=True)
        path = path.joinpath(
            f'{datetime.utcnow().strftime("%Y-%m-%d")}_[query={query if query else None}].log'
        )
        file_handler = logging.FileHandler(path.absolute())
        file_handler.setFormatter(self._formatter)
        self.logger.addHandler(file_handler)

    def log_message(self, msg: Message, channel: Channel, user: User):
        self.logger.info("{}: {}".format(utils.get_display_name(user), msg.text))


class TelegramInteractionHelper:
    def __init__(self, client: TelegramClient, logger, log_helper: TelegramLogHelper):
        self.client = client
        self.logger = logger
        self.log_helper = log_helper

    async def send_to_ifttt_async(self, event, key, header, body, url):
        payload = {"value1": header, "value2": body, "value3": url}
        endpoint = f"https://maker.ifttt.com/trigger/{event}/with/key/{key}"
        async with aiohttp.ClientSession() as session:
            async with session.post(endpoint, data=payload) as resp:
                self.logger.info(f"[{url}] {header}{body}\nIFTTT status: {resp.status}")

    async def iter_messages_async(
        self,
        chat,
        user,
        query,
        output,
        print_stat=False,
        cut_func=None,
        offset_date=None,
        min_date=None,
    ):
        if print_stat:
            counter = Counter()
        async for msg in self.client.iter_messages(
            chat, from_user=user, offset_date=offset_date
        ):
            if min_date and msg.date and msg.date.replace(tzinfo=None) < min_date:
                break
            if not query or (msg.text and query in msg.text):
                if isinstance(output, Channel):
                    url = get_url(chat, msg)
                    await self.client.send_message(
                        output,
                        "{}:\n{}\n{}".format(
                            msg.date,
                            msg.text if cut_func is None else cut_func(msg.text),
                            url,
                        ),
                    )
                else:
                    sender = user
                    if sender is None:
                        if msg.post:
                            sender = chat
                        elif msg.from_id is None:
                            self.logger.debug(msg)
                            continue
                        else:
                            sender = await self.client.get_entity(msg.from_id)
                    self.log_helper.log_message(msg, chat, sender)
                if print_stat:
                    counter[msg.date.hour] += 1

        if print_stat:
            total = sum(counter.values())
            for hour in range(24):
                print("{}: {}".format(hour, floor(counter[hour] / total * 100) * "="))

    async def get_entity(self, entity_like):
        try:
            return await self.client.get_entity(int(entity_like))
        except Exception:
            return await self.client.get_entity(entity_like)

    def is_same_entity(self, entity: EntitiesLike, other):
        return (
            str(entity.id) == str(other)
            or str(entity.username) == str(other)
            or f"-100{entity.id}" == str(other)
            or utils.get_display_name(entity) == str(other)
        )

    async def get_sender(self, msg: Message):
        sender = await msg.get_sender()
        if sender is None:
            if msg.post_author:
                return msg.post_author
            if msg.peer_id:
                return utils.get_display_name(msg.peer_id)
            return "Unknown"
        return utils.get_display_name(sender)

    def parse_msg(self, msg, key, regex):
        match = re.search(rf"{re.escape(key)}=({regex})", msg)
        if match is not None:
            return match.groups()[0]
        return None

    def clean_entity(self, msg, key):
        return re.sub(rf"{re.escape(key)}=([0-9a-zA-Z_\-]+)", "", msg)

    async def parse_entity(self, msg: str, entity_name: str):
        value = self.parse_msg(msg, entity_name, r"[0-9a-zA-Z_\-]+")
        if value is not None:
            return await self.get_entity(value)
        return None
