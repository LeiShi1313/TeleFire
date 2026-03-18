import os
import re
import sys
import pickle
import logging
import asyncio
from pathlib import Path
from math import floor
from datetime import datetime
from collections import Counter

import aiohttp
from telethon import utils
from telethon.sync import TelegramClient
from telethon.hints import EntitiesLike
from telethon.tl.types import Channel, Message, User
from mautrix.client import Client as MatrixClient
from mautrix.api import HTTPAPI
from mautrix.types import UserID, FilterID, Filter, RoomID, EventID, EventType
# from mautrix.types import Message as MatrixMessage

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
        path = Path('logs').joinpath(method)
        if channel:
            path = path.joinpath(channel.title)
        if user:
            path = path.joinpath(utils.get_display_name(user))
        path.mkdir(parents=True, exist_ok=True)
        path = path.joinpath(f'{datetime.utcnow().strftime("%Y-%m-%d")}_[query={query if query else None}].log')
        file_handler = logging.FileHandler(path.absolute())
        file_handler.setFormatter(self._logFormatter)
        self._logger.addHandler(file_handler)

    def _log_message(self, msg: Message, channel: Channel, user: User):
        self._logger.info("{}: {}".format(
            # msg.date,
            # get_url(channel, msg),
            utils.get_display_name(user),
            msg.text))

    async def _send_to_ifttt_async(self, event, key, header, body, url):
        payload = {'value1': header, 'value2': body, 'value3': url}
        u = 'https://maker.ifttt.com/trigger/{}/with/key/{}'.format(event, key)
        async with aiohttp.ClientSession() as session:
            async with session.post(u, data=payload) as resp:
                self._logger.info("[{}] {}{}\nIFTTT status: {}".format(url, header, body, resp.status))

    async def _iter_messages_async(self, chat, user, query, output, print_stat=False, cut_func=None, offset_date=None, min_date=None):
        if print_stat:
            counter = Counter()
        async for msg in self._client.iter_messages(chat, from_user=user, offset_date=offset_date):
            if min_date and msg.date and msg.date.replace(tzinfo=None) < min_date:
                break
            if not query or (msg.text and query in msg.text):
                if isinstance(output, Channel):
                    url = get_url(chat, msg)
                    await self._client.send_message(output, "{}:\n{}\n{}".format(msg.date, msg.text if cut_func is None else cut_func(msg.text), url))
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
            entity = await self._client.get_entity(int(entity_like))
        except Exception as e:
            entity = await self._client.get_entity(entity_like)
        return entity

    def _is_same_entity(self, entity: EntitiesLike, other):
        return str(entity.id) == str(other) or str(entity.username) == str(other) or f"-100{entity.id}" == str(other) or utils.get_display_name(entity) == str(other)

    async def _get_sender(self, msg: Message):
        sender = await msg.get_sender()
        if sender is None:
            if msg.post_author:
                return msg.post_author
            elif msg.peer_id:
                return utils.get_display_name(msg.peer_id)
            else:
                return 'Unknown'
        return utils.get_display_name(sender)

    def _parse_msg(self, msg, key, regex):
        m = re.search(r'{}=({})'.format(key, regex), msg)
        if m is not None:
            return m.groups()[0]
        return None

    def _clean_entity(self, msg, key):
        return re.sub(r'{}=({})'.format(key, r'[0-9a-zA-Z_\-]+'), '', msg)

    async def _parse_entity(self, msg: str, entity_name: str):
        m = self._parse_msg(msg, entity_name, r'[0-9a-zA-Z_\-]+')
        if m is not None:
            return await self._get_entity(m)
        return None


class Matrix(object):
    room_name_cache = {}

    def __init__(self, log_level='info'):
        self.room_name_cache = pickle.load(open('room_name_cache.pkl', 'rb')) if os.path.exists('room_name_cache.pkl') else {}
        base_url = os.environ.get("MATRIX_BASE_URL")
        self.user_id = os.environ.get("MATRIX_USER_ID")
        self.password = os.environ.get("MATRIX_PASSWORD")
        self.loop = asyncio.get_event_loop()
        if not base_url or not self.user_id or not self.password:
            raise ValueError("Please set MATRIX_BASE_URL, MATRIX_USER_ID and MATRIX_PASSWORD as environment variables!")
        self._client = MatrixClient(api=HTTPAPI(base_url))
        # self._client.login(username, password)
        self._logger = logging.getLogger(__name__)
        self._logger.setLevel(logging.INFO if log_level=='info' else logging.DEBUG)
        # logFormatter = logging.Formatter("%(asctime)s [%(name)s] [%(levelname)s] %(message)s")
        self._logFormatter = logging.Formatter("%(message)s")
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setFormatter(self._logFormatter)
        self._logger.addHandler(console_handler)

    def login(self):
        async def _login():
            await self._client.login(identifier=self.user_id, password=self.password)
            self.profile = await self._client.get_profile(self.user_id)
            self._logger.info(f"Logged in as {self.profile.displayname}")
        self.loop.run_until_complete(_login())

    def start(self, filter_data: FilterID | Filter | None = None):
        self.login()
        async def _start():
            await self._client.start(filter_data=filter_data)
        self.loop.run_until_complete(_start())

    def stop(self):
        self._client.api.session.close()
        self._client.stop()

    def update_room_name_cache(self, room_id: RoomID, name: str):
        self.room_name_cache[room_id] = name
        pickle.dump(self.room_name_cache, open('room_name_cache.pkl', 'wb'))

    async def get_room_displayname(self, room_id: RoomID):
        if room_id in self.room_name_cache:
            return self.room_name_cache[room_id]
        try:
            state = await self._client.get_state_event(room_id=room_id, event_type=EventType.ROOM_NAME)
            if state is not None:
                self.update_room_name_cache(room_id, state.name)
                return state.name
        except Exception as e:
            self._logger.debug(f"Error getting room name: {e}")
            pass
        try:
            state = await self._client.get_state_event(room_id=room_id, event_type=EventType.ROOM_CANONICAL_ALIAS)
            if state is not None:
                self.update_room_name_cache(room_id, state.name)
                return state.name
        except Exception as e:
            self._logger.debug(f"Error getting room canonical alias: {e}")
            pass
        return room_id

class Commands(object):
    pass


class PluginMount(type):
    def __init__(cls, name, bases, attrs):
        super(PluginMount, cls).__init__(name, bases, attrs)
        command_name = cls.command_name if hasattr(cls, 'command_name') else camel_to_snake(cls.__name__)
        setattr(Commands, command_name, cls)
