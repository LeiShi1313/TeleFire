import re
import sys
import fire
import asyncio
import aiohttp
import logging
from math import floor
from telethon import utils
from datetime import timedelta
from collections import Counter
from subprocess import check_output
from telethon.sync import TelegramClient, events
from telethon.tl.functions.messages import SearchRequest
from telethon.tl.functions.channels import CreateChannelRequest
from telethon.tl.types import InputMessagesFilterEmpty, MessageEntityTextUrl, Channel
from telethon.tl.types import PeerUser, PeerChat, PeerChannel


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

    def _set_file_handler(self, method, channel=None, user=None, query=None):
        fileHandler = logging.FileHandler(
                "{}{}{}{}.log".format(
                    method,
                    '_[{}]'.format(channel.title) if channel else '',
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
        m = re.search(r'^\/([\d]+[s|m|h|d]) (.*)$', text, re.DOTALL)
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

    def _generate_message_url(self, channel, msg):
        if channel.username is None:
            return "https://t.me/c/{}/{}".format(channel.id, msg.id)
        else:
            return "https://t.me/{}/{}".format(channel.username, msg.id)

    async def _get_entity(self, entity_like):
        try:
            entity = await self._client.get_entity(entity_like)
        except Exception as e:
            entity = await self._client.get_entity(int(entity_like))
        return entity

    async def _send_to_ifttt_async(self, event, key, header, body, url):
        payload = {'value1': header, 'value2': body, 'value3': url}
        u = 'https://maker.ifttt.com/trigger/{}/with/key/{}'.format(event, key)
        async with aiohttp.ClientSession() as session:
            async with session.post(u, data=payload) as resp:
                self._logger.info("[{}] {}{}\nIFTTT status: {}".format(url, header, body, resp.status))

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
            if not query or (msg.text and query in msg.text):
                self._log_message(msg, channel, user)
                await msg.delete()

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

    async def _list_messages_async(self, chat, user, output, print_stat=False):
        channel = await self._client.get_entity(chat)
        if user is not None:
            try:
                user = await self._client.get_entity(user)
            except ValueError as e:
                if user.isdecimal():
                    user = await self._client.get_entity(int(user))
                else:
                    raise e

        if output == 'channel':
            result = await self._client(CreateChannelRequest(
                "List Messages",
                "Messages For {} in {}".format(utils.get_display_name(user), channel.title)))
            created_channel = result.chats[0]
            self._logger.info("Channel: {} created.".format(created_channel.title))
            await self._iter_messages_async(channel, user, '', created_channel, print_stat)
        else:
            self._set_file_handler('list_messages', channel, user)

            self._logger.debug(channel)
            self._logger.debug(user)
            await self._iter_messages_async(channel, user, '', 'log', print_stat)
        self._logger.info("Listing all messages {}in {}".format(
            'for {} '.format(utils.get_display_name(user)) if user else '', channel.title))

    async def _search_messages_async(self, chat, query, slow, limit, user, output):
        _filter = InputMessagesFilterEmpty()
        peer = await self._client.get_entity(chat)
        if user is not None:
            user = await self._client.get_entity(user)

        self._set_file_handler('search_messages', peer, user, query)

        if slow:
            if output == 'channel':
                result = await self._client(CreateChannelRequest(
                    "Search Messages",
                    "Messages in {}".format(peer.title)))
                output = result.chats[0]
                self._logger.info("Channel: {} created.".format(output.title))
            await self._iter_messages_async(peer, user, query, output)
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

    def search_messages(self, chat, query, slow=False, limit=100, user=None, output='log'):
        with self._client:
            self._client.loop.run_until_complete(
                    self._search_messages_async(chat, query, slow, limit, user, output))

    def list_messages(self, chat, user=None, output='log', print_stat=False):
        with self._client:
            self._client.loop.run_until_complete(
                    self._list_messages_async(chat, user, output, print_stat))

    def delete_all(self, chat, query=''):
        with self._client:
            self._client.loop.run_until_complete(
                    self._delete_all_async(chat, query))

    def get_all_chats(self):
        with self._client:
            self._client.loop.run_until_complete(self._get_all_chats())

    def words_to_ifttt(self, event, key, *words):
        @self._client.on(events.NewMessage)
        async def _inner(evt):
            if any(w.lower() in evt.raw_text.lower() for w in words):
                msg = evt.message
                channel = await self._client.get_entity(msg.to_id)
                header = "{}在{}说了: ".format(' '.join([msg.sender.first_name, msg.sender.last_name]),channel.title)
                body = evt.raw_text[:20] + ('...' if len(evt.raw_text) > 20 else '')
                url = self._generate_message_url(channel, msg)
                await self._send_to_ifttt_async(event, key, header, body, url)

        self._set_file_handler('words_to_ifttt')
        self._logger.info("Sending messages to IFTTT for words:{}".format(words))
        self._client.start()
        self._client.run_until_disconnected()

    async def _markdown_mode(self, msg):
        if not msg.out:
            return
        channel = await self._client.get_entity(msg.to_id)
        self._logger.info(msg)
        try:
            await self._client.edit_message(msg, msg.text[4:], parse_mode='markdown')
        except Exception as e:
            self._logger.info(e)

    async def _shiny_mode(self, msg):
        if not msg.out:
            return
        channel = await self._client.get_entity(msg.to_id)
        self._logger.info(msg)
        try:
            t = msg.text[7:]
            for i in range(200):
                await self._client.edit_message(msg, "**"+t+"**", parse_mode='md')
                await asyncio.sleep(1)
                await self._client.edit_message(msg, t, parse_mode=None)
                await asyncio.sleep(1)
            await self._client.edit_message(msg, t, parse_mode=None)
        except Exception as e:
            self._logger.info(e)

    async def _auto_delete_mode(self, event, msg):
        if not msg.out:
            return
        channel = await event.get_chat()
        user = await event.get_sender()
        self._log_message(msg, channel, user)
        t, text = self._parse_auto_delete_message(msg.text)
        await self._auto_delete_async(msg, t, text)

    async def _search_mode(self, msg):
        raw_params = msg.text.split(' ')[1:]
        if len(raw_params) == 2:
            c, user, query = raw_params + ['']
        elif len(raw_params) == 3:
            c, user, query = raw_params
        else:
            self._logger.info("Unknown command: {}".format(msg.text))
            self._logger.debug("{}".format(msg))
        await msg.delete()
        try:
            channel = await self._get_entity(msg.to_id if c == 'this' else c)
            user = await self._get_entity(user)
        except Exception as e:
            self._logger.info(e)
            return

        try:
            result = await self._client(CreateChannelRequest(
                "{} in {}".format(utils.get_display_name(user), channel.title),
                "query={}".format(query)))
            created_channel = result.chats[0]
        except Exception as e:
            self._logger.error(e)
        self._logger.info("Channel: {} created.".format(created_channel.id))

        await self._iter_messages_async(channel, user, query, created_channel)

    async def _archive_mode_async(self, msg, group_name, docker_name, url):
        try:
            cmd = 'echo "{}" | docker exec -i {} /bin/archive'.format(url, docker_name)
            self._logger.info("Archiving: {}".format(cmd))
            process = await asyncio.create_subprocess_shell(cmd, stdout=asyncio.subprocess.PIPE)
            stdout, _ = await process.communicate()
            await self._client.send_message(group_name, stdout.decode('utf-8')[:4000], reply_to=msg)
            self._logger.info("Archive completed: {}".format(url))
        except Exception as e:
            self._logger.info(e)


    def plus_mode(self):
        @self._client.on(events.NewMessage)
        async def _inner(evt):
            msg = evt.message
            if msg.text:
                if re.search(r'^\/([\d]+[s|m|h|d]) (.*)$', msg.text, re.DOTALL):
                    self._logger.info("Received auto delete message: {}".format(msg.text))
                    await self._auto_delete_mode(evt, msg)
                if msg.text.startswith('/md'):
                    self._logger.info("Received markdown mode message: {}".format(msg.text))
                    await self._markdown_mode(msg)
                elif msg.text.startswith('/shiny'):
                    self._logger.info("Received shiny message: {}".format(msg.text))
                    await self._shiny_mode(msg)
                elif msg.text.startswith('/search'):
                    self._logger.info("Received search message: {}".format(msg.text))
                    await self._search_mode(msg)

        self._set_file_handler('plus_mode')
        self._client.start()
        self._client.run_until_disconnected()

    def special_attention_mode(self, event, key, *people):
        @self._client.on(events.NewMessage)
        async def _inner(evt):
            msg = evt.message
            sender = evt.message.sender
            if sender and any(sender.id==p or sender.username==p for p in people):
                channel = await self._client.get_entity(msg.to_id)
                header = "{}在{}说了: ".format(' '.join([msg.sender.first_name, msg.sender.last_name]),channel.title)
                body = evt.raw_text[:20] + ('...' if len(evt.raw_text) > 20 else '')
                url = self._generate_message_url(channel, msg)
                await self._send_to_ifttt_async(event, key, header, body, url)

        self._set_file_handler('special_attention_mode')
        self._logger.info("Sending messages to IFTTT for people:{}".format(people))
        self._client.start()
        self._client.run_until_disconnected()

    def archive_mode(self, docker_name, me, group_name='ArchiveIt'):
        @self._client.on(events.NewMessage)
        async def _inner(evt):
            msg = evt.message
            sender = evt.message.sender
            if sender and sender.username==me:
                self._logger.info("Received: {}".format(msg.text))
                for match in matcher.findall(msg.text):
                    self._logger.info("Matched: {}".format(match))
                    await self._archive_mode_async(msg, group_name, docker_name, match)


        matcher = re.compile(r"(?P<url>https?://[^\s]+)")
        self._set_file_handler('archive_mode')
        self._logger.info("Archiving pages for: {}".format(me))
        self._client.start()
        self._client.run_until_disconnected()

    def log_chat(self, chat):
        @self._client.on(events.NewMessage)
        async def _inner(evt):
            msg = evt.message
            channel = await self._client.get_entity(msg.to_id)
            if channel.username == chat:
                if msg.media:
                    self._logger.info(msg)

        self._set_file_handler('log_chat')
        self._client.start()
        self._client.run_until_disconnected()


if __name__ == '__main__':
    fire.Fire(Telegram, name='tg')
