import sys
import logging
from math import floor
from collections import Counter

import aiohttp
from telethon import utils
from telethon.sync import TelegramClient
from telethon.tl.types import Channel, Message, User

from utils import get_url, camel_to_snake


class PluginMount(type):
    def __init__(cls, name, bases, attrs):
        super(PluginMount, cls).__init__(name, bases, attrs)
        name = cls.name if hasattr(cls, 'name') else camel_to_snake(cls.__name__)
        if bases[0] != object:
            setattr(bases[0], name, getattr(cls, 'action', lambda: None))


class Telegram(object, metaclass=PluginMount):
    def __init__(self, api_id, api_hash, session='test', log_level='info'):
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
                    url = self._generate_message_url(chat, msg)
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