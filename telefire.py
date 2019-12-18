import sys
import fire
import aiohttp
import logging
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

    async def _get_all_chats(self):
        async for dialog in self._client.iter_dialogs():
            self._logger.info('{:>14}: {}'.format(dialog.id, dialog.title))


    async def _delete_all_async(self, chat=''):
        user = await self._client.get_me()
        channel = await self._client.get_entity(chat)

        fileHandler = logging.FileHandler("delete_messages_[{}].log".format(channel.title))
        fileHandler.setFormatter(self._logFormatter)
        self._logger.addHandler(fileHandler)
        self._logger.info("Deleting all messages for {} in {}".format(
            utils.get_display_name(user), channel.title))
        async for msg in self._client.iter_messages(channel, from_user=user):
            self._logger.info("{} [{}] {}: {}".format(
                msg.date,
                _get_url(channel, msg),
                utils.get_display_name(user),
                msg.text))
            await msg.delete()

    async def _list_messages_async(self, chat, user=None):
        channel = await self._client.get_entity(chat)
        if user is not None:
            user = await self._client.get_entity(user)

        fileHandler = logging.FileHandler("list_messages_[{}]{}.log".format(
            channel.title,
            '_[{}]'.format(utils.get_display_name(user)) if user else ''))
        fileHandler.setFormatter(self._logFormatter)
        self._logger.addHandler(fileHandler)

        self._logger.debug(channel)
        self._logger.debug(user)
        self._logger.info("Listing all messages {}in {}".format(
            'for {} '.format(utils.get_display_name(user)) if user else '', channel.title))
        async for msg in self._client.iter_messages(channel, from_user=user):
            self._logger.info("{} [{}] {}: {}".format(
                msg.date,
                _get_url(channel, msg),
                utils.get_display_name(user) if user \
                        else utils.get_display_name(
                            await self._client.get_entity(msg.from_id)),
                msg.text))

    async def _search_messages_async(self, peer, query, slow, limit, user):
        _filter = InputMessagesFilterEmpty()
        peer = await self._client.get_entity(peer)
        if user is not None:
            user = await self._client.get_entity(user)

        fileHandler = logging.FileHandler("search_messages_[{}]_[query={}]{}.log".format(
            peer.title, query,
            '_[{}]'.format(utils.get_display_name(user)) if user is not None else ''))
        fileHandler.setFormatter(self._logFormatter)
        self._logger.addHandler(fileHandler)

        if slow:
            async for msg in self._client.iter_messages(peer, from_user=user):
                if msg and msg.text and query in msg.text:
                    self._logger.info("{} [{}] {}: {}".format(
                        msg.date,
                        _get_url(peer, msg),
                        utils.get_display_name(user) if user \
                                else utils.get_display_name(
                                    await self._client.get_entity(msg.from_id)),
                        msg.text))
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
                user = await self._client.get_entity(msg.from_id)
                self._logger.info("{} [{}] {}: {}".format(
                    msg.date,
                    _get_url(peer, msg),
                    utils.get_display_name(user),
                    msg.message))

    def search_messages(self, peer, query, slow=False, limit=100, from_id=None):
        with self._client:
            self._client.loop.run_until_complete(
                    self._search_messages_async(peer, query, slow, limit, from_id))

    def list_messages(self, chat='', user=None):
        if not chat:
            self._logger.error("Chat cannot be enpty!")
            return
        with self._client:
            self._client.loop.run_until_complete(
                    self._list_messages_async(chat, user))

    def delete_all(self, chat=''):
        if not chat:
            self._logger.error("Chat cannot be enpty!")
            return
        with self._client:
            self._client.loop.run_until_complete(
                    self._delete_all_async(chat))

    def get_all_chats(self):
        with self._client:
            self._client.loop.run_until_complete(self._get_all_chats())


if __name__ == '__main__':
    fire.Fire(Telegram, name='tg')
