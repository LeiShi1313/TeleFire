import os
import re
import sys
import logging
from math import floor
from collections import Counter

import aiohttp
from telethon import utils
from telethon.sync import TelegramClient
from telethon.tl.types import Channel, Message, User

from utils import get_url, camel_to_snake


class Telegram(object):
    def __init__(self, session='test', log_level='info'):
        api_id = os.environ.get("TELEGRAM_API_ID")
        api_hash = os.environ.get("TELEGRAM_API_HASH")
        if not api_id or not api_hash:
            raise ValueError("Please set TELEGRAM_API_ID and TELEGRAM_API_HASH as environment variables!")
        self._client = TelegramClient(session, api_id, api_hash)

        self._logger = logging.getLogger(__name__)
        self._logger.setLevel(logging.INFO if log_level=='info' else logging.DEBUG)
        # logFormatter = logging.Formatter("%(asctime)s [%(name)s] [%(levelname)s] %(message)s")
        self._logFormatter = logging.Formatter("%(message)s")
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setFormatter(self._logFormatter)
        self._logger.addHandler(console_handler)

    def _set_file_handler(self, method, channel=None, user=None, query=None):
        file_handler = logging.FileHandler(
                "{}{}{}{}.log".format(
                    method,
                    '_[{}]'.format(channel.title) if channel else '',
                    '_[{}]'.format(utils.get_display_name(user)) if user else '',
                    '_[query={}]'.format(query) if query else ''))
        file_handler.setFormatter(self._logFormatter)
        self._logger.addHandler(file_handler)

    def _log_message(self, msg: Message, channel: Channel, user: User):
        self._logger.info("{} [{}] [{}]: {}".format(
            msg.date,
            get_url(channel, msg),
            utils.get_display_name(user),
            msg.text))

    async def _send_to_ifttt_async(self, event, key, header, body, url):
        payload = {'value1': header, 'value2': body, 'value3': url}
        u = 'https://maker.ifttt.com/trigger/{}/with/key/{}'.format(event, key)
        async with aiohttp.ClientSession() as session:
            async with session.post(u, data=payload) as resp:
                self._logger.info("[{}] {}{}\nIFTTT status: {}".format(url, header, body, resp.status))

    async def _iter_messages_async(self, chat, user, query, output, print_stat=False):
        if print_stat:
            counter = Counter()
        async for msg in self._client.iter_messages(chat, from_user=user):
            if not query or (msg.text and query in msg.text):
                if isinstance(output, Channel):
                    url = get_url(chat, msg)
                    await self._client.send_message(output, "{}:\n{}\n{}".format(msg.date, msg.text, url))
                else:
                    sender = user
                    if sender is None:
                        if msg.post:
                            sender = chat
                        elif msg.from_id == None:
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
                print("{}: {}".format(hour, floor(counter[hour] / total * 100) * '='))

    async def _get_entity(self, entity_like):
        try:
            entity = await self._client.get_entity(entity_like)
        except Exception as e:
            entity = await self._client.get_entity(int(entity_like))
        return entity

    def _parse_msg(self, msg, key, regex):
        m = re.search(r'{}=({})'.format(key, regex), msg)
        if m is not None:
            return m.groups()[0]
        return None

    async def _parse_entity(self, msg: str, entity_name: str):
        m = self._parse_msg(msg, entity_name, r'[0-9a-zA-Z_\-]+')
        if m is not None:
            return await self._get_entity(m)
        return None


class Commands(object):
    pass


class PluginMount(type):
    def __init__(cls, name, bases, attrs):
        super(PluginMount, cls).__init__(name, bases, attrs)
        command_name = cls.command_name if hasattr(cls, 'command_name') else camel_to_snake(cls.__name__)
        setattr(Commands, command_name, cls)