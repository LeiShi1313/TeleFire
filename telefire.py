import re
import sys
import fire
import asyncio
import aiohttp
import logging
from datetime import timedelta
from telethon import utils
from telethon.sync import TelegramClient, events
from telethon.tl.types import InputMessagesFilterEmpty
from telethon.tl.functions.messages import SearchRequest


def _get_url(channel, msg):
    if channel.username:
        return "https://t.me/{}/{}".format(channel.username, msg.id)
    else:
        return "https://t.me/c/{}/{}".format(channel.id, msg.id)


class Telegram(object):
    def __init__(self, api_id, api_hash, session='test', log_level='info'):
        self._client = TelegramClient(session, api_id, api_hash)

        self._logger = logging.getLogger(__name__)
        self._logger.setLevel(logging.INFO if log_level=='info' else logging.DEBUG)
        # logFormatter = logging.Formatter("%(asctime)s [%(name)s] [%(levelname)s] %(message)s")
        self._logFormatter = logging.Formatter("%(message)s")
        consoleHandler = logging.StreamHandler(sys.stdout)
        consoleHandler.setFormatter(self._logFormatter)
        self._logger.addHandler(consoleHandler)

    def _set_file_handler(self, method, channel, user='', query=''):
        fileHandler = logging.FileHandler(
                "{}_[{}]{}{}.log".format(
                    method,
                    channel.title,
                    '_[{}]'.format(utils.get_display_name(user)) if user else '',
                    '_[query={}]'.format(query) if query else ''))
        fileHandler.setFormatter(self._logFormatter)
        self._logger.addHandler(fileHandler)

    def _log_message(self, msg, channel, user):
        self._logger.info("{} [{}] [{}]: {}".format(
            msg.date,
            _get_url(channel, msg),
            utils.get_display_name(user),
            msg.text))

    def _parse_auto_delete_message(self, text):
        m = re.search(r'^([\d]+[s|m|h|d]) (.*)$', text, re.DOTALL)
        if m:
            num = int(m.group(1)[:-1]) if m.group(1)[:-1].isnumeric() else None
            net = m.group(1)[-1]
            if num is not None:
                if net == 'd':
                    num *= 86400
                elif net == 'h':
                    num *= 3600
                elif net == 'm':
                    num *= 60
            return num, m.group(2)
        return None, None

    async def _auto_delete_async(self, msg, t, text):
        template = "{}\n==========\nTTL: {}"
        delta = timedelta(seconds=t)
        while delta > timedelta(0):
            await msg.edit(text=template.format(text, str(delta)))
            if delta.days > 1:
                await asyncio.sleep(86400)
                delta -= timedelta(days=1)
            elif delta.seconds >= 120:
                await asyncio.sleep(60)
                delta -= timedelta(seconds=60)
            elif delta.seconds >= 20:
                await asyncio.sleep(10)
                delta -= timedelta(seconds=10)
            else:
                await asyncio.sleep(1)
                delta -= timedelta(seconds=1)
        await msg.edit(text='[DELETED MESSAGE]')
        await asyncio.sleep(60)
        await msg.delete()

    async def _get_all_chats(self):
        async for dialog in self._client.iter_dialogs():
            self._logger.info('{:>14}: {}'.format(dialog.id, dialog.title))

    async def _delete_all_async(self, chat, query):
        user = await self._client.get_me()
        channel = await self._client.get_entity(chat)

        self._set_file_handler('delete_all', channel, user)
        self._logger.info("Deleting all messages for {} in {}".format(
            utils.get_display_name(user), channel.title))
        async for msg in self._client.iter_messages(channel, from_user=user):
            if not query or query in msg.text:
                self._log_message(msg, channel, user)
                await msg.delete()

    async def _list_messages_async(self, chat, user=None):
        channel = await self._client.get_entity(chat)
        if user is not None:
            user = await self._client.get_entity(user)

        self._set_file_handler('list_messages', channel, user)

        self._logger.debug(channel)
        self._logger.debug(user)
        self._logger.info("Listing all messages {}in {}".format(
            'for {} '.format(utils.get_display_name(user)) if user else '', channel.title))
        async for msg in self._client.iter_messages(channel, from_user=user):
            sender = user
            if sender is None:
                sender = await self._client.get_entity(msg.from_id)
            self._log_message(msg, channel, sender)

    async def _search_messages_async(self, peer, query, slow, limit, user):
        _filter = InputMessagesFilterEmpty()
        peer = await self._client.get_entity(peer)
        if user is not None:
            user = await self._client.get_entity(user)

        self._set_file_handler('search_messages', peer, user, query)

        if slow:
            async for msg in self._client.iter_messages(peer, from_user=user):
                if msg and msg.text and query in msg.text:
                    sender = user
                    if sender is None:
                        sender = await self._client.get_entity(msg.from_id)
                    self._log_message(msg, peer, sender)
        else:
            search_request = SearchRequest(
                    peer=peer,
                    q=query,
                    filter=_filter,
                    min_date=None,
                    max_date=None,
                    offset_id=0,
                    add_offset=0,
                    limit=limit,
                    max_id=0,
                    min_id=0,
                    hash=0,
                    from_id=user)
            result = await self._client(search_request)
            for msg in result.messages:
                sender = user
                if sender is None:
                    sender = await self._client.get_entity(msg.from_id)
                self._log_message(msg, peer, sender)

    def search_messages(self, peer, query, slow=False, limit=100, from_id=None):
        with self._client:
            self._client.loop.run_until_complete(
                    self._search_messages_async(peer, query, slow, limit, from_id))

    def list_messages(self, chat, user=None):
        with self._client:
            self._client.loop.run_until_complete(
                    self._list_messages_async(chat, user))

    def delete_all(self, chat, query=''):
        with self._client:
            self._client.loop.run_until_complete(
                    self._delete_all_async(chat, query))

    def get_all_chats(self):
        with self._client:
            self._client.loop.run_until_complete(self._get_all_chats())

    def auto_delete(self):
        @self._client.on(events.NewMessage(pattern=r'^[\d]+[s|m|h|d] '))
        async def _inner(event):
            msg = event.message
            if not msg.out:
                return
            channel = await event.get_chat()
            user = await event.get_sender()
            self._log_message(msg, channel, user)
            t, text = self._parse_auto_delete_message(msg.text)
            await self._auto_delete_async(msg, t, text)

        self._client.start()
        self._client.run_until_disconnected()


if __name__ == '__main__':
    fire.Fire(Telegram, name='tg')
