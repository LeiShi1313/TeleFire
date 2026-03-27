import asyncio
import inspect
import logging
import re
from collections import Counter
from datetime import datetime
from math import floor
from pathlib import Path

import aiohttp
from telethon import utils
from telethon.hints import EntitiesLike
from telethon.tl.types import Channel, Message, User

from telefire.telegram.config import TelegramRuntimeConfig
from telefire.telegram.service import TelegramService
from telefire.utils import get_url


class TelegramCommand:
    def __init__(self, session: str = "test", log_level: str = "info"):
        self.telegram = TelegramService(
            TelegramRuntimeConfig.from_env(session=session),
            log_level=log_level,
        )
        self._client = self.telegram.client
        self._logger = self.telegram.logger
        self._log_formatter = logging.Formatter("%(message)s")

    def _set_file_handler(self, method, channel=None, user=None, query=None):
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
        file_handler.setFormatter(self._log_formatter)
        self._logger.addHandler(file_handler)

    def _log_message(self, msg: Message, channel: Channel, user: User):
        self._logger.info(
            "{}: {}".format(
                utils.get_display_name(user),
                msg.text,
            )
        )

    async def _send_to_ifttt_async(self, event, key, header, body, url):
        payload = {"value1": header, "value2": body, "value3": url}
        endpoint = f"https://maker.ifttt.com/trigger/{event}/with/key/{key}"
        async with aiohttp.ClientSession() as session:
            async with session.post(endpoint, data=payload) as resp:
                self._logger.info(
                    f"[{url}] {header}{body}\nIFTTT status: {resp.status}"
                )

    async def _iter_messages_async(
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
        async for msg in self._client.iter_messages(
            chat, from_user=user, offset_date=offset_date
        ):
            if min_date and msg.date and msg.date.replace(tzinfo=None) < min_date:
                break
            if not query or (msg.text and query in msg.text):
                if isinstance(output, Channel):
                    url = get_url(chat, msg)
                    await self._client.send_message(
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
                            self._logger.debug(msg)
                            continue
                        else:
                            sender = await self._client.get_entity(msg.from_id)
                    self._log_message(msg, chat, sender)
                if print_stat:
                    counter[msg.date.hour] += 1
        if print_stat:
            total = sum(counter.values())
            for hour in range(24):
                print("{}: {}".format(hour, floor(counter[hour] / total * 100) * "="))

    async def _get_entity(self, entity_like):
        try:
            return await self._client.get_entity(int(entity_like))
        except Exception:
            return await self._client.get_entity(entity_like)

    def _is_same_entity(self, entity: EntitiesLike, other):
        return (
            str(entity.id) == str(other)
            or str(entity.username) == str(other)
            or f"-100{entity.id}" == str(other)
            or utils.get_display_name(entity) == str(other)
        )

    async def _get_sender(self, msg: Message):
        sender = await msg.get_sender()
        if sender is None:
            if msg.post_author:
                return msg.post_author
            if msg.peer_id:
                return utils.get_display_name(msg.peer_id)
            return "Unknown"
        return utils.get_display_name(sender)

    def _parse_msg(self, msg, key, regex):
        match = re.search(rf"{key}=({regex})", msg)
        if match is not None:
            return match.groups()[0]
        return None

    def _clean_entity(self, msg, key):
        return re.sub(rf"{key}=([0-9a-zA-Z_\-]+)", "", msg)

    async def _parse_entity(self, msg: str, entity_name: str):
        value = self._parse_msg(msg, entity_name, r"[0-9a-zA-Z_\-]+")
        if value is not None:
            return await self._get_entity(value)
        return None

    async def _invoke_async(self, action):
        result = action() if callable(action) else action
        if inspect.isawaitable(result):
            return await result
        return result

    def run_telegram(self, action):
        return asyncio.run(
            self.telegram.run_once(lambda _: self._invoke_async(action))
        )

    def run_telegram_forever(self, setup=None):
        async def _setup(_):
            if setup is None:
                return None
            return await self._invoke_async(setup)

        return asyncio.run(
            self.telegram.run_forever(setup=_setup if setup else None)
        )
