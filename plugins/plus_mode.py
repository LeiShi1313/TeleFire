import re
import traceback
import asyncio
from datetime import timedelta

from telethon import utils
from telethon.sync import events
from telethon.tl.functions.channels import CreateChannelRequest

from plugins.base import Telegram, PluginMount


class PlusMode(Telegram, metaclass=PluginMount):
    command_name = 'plus_mode'

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

    async def _auto_delete_mode(self, event, msg):
        if not msg.out:
            return
        channel = await event.get_chat()
        user = await event.get_sender()
        self._log_message(msg, channel, user)
        t, text = self._parse_auto_delete_message(msg.text)
        await self._auto_delete_async(msg, t, text)

    async def _markdown_mode(self, msg):
        if not msg.out:
            return
        self._logger.info(msg)
        try:
            await self._client.edit_message(msg, msg.text[4:], parse_mode='markdown')
        except Exception as e:
            self._logger.info(e)

    async def _getid_mode(self, msg):
        try:
            await msg.delete()
            me = self._client.get_me()
            sender = await msg.get_sender()
            await self._client.send_message(me, f'id: `{sender.id}`\nusername: `{sender.username}`')
        except Exception as _:
            traceback.print_exc()

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

    def __call__(self):
        @self._client.on(events.NewMessage)
        async def _inner(evt):
            msg = evt.message
            channel = await evt.get_chat()
            if msg.text:
                if re.search(r'^\/([\d]+[s|m|h|d]) (.*)$', msg.text, re.DOTALL):
                    self._logger.info("Received auto delete message: {}".format(msg.text))
                    await self._auto_delete_mode(evt, msg)
                elif msg.text.startswith('/md'):
                    self._logger.info("Received markdown mode message: {}".format(msg.text))
                    await self._markdown_mode(msg)
                elif msg.text.startswith('/search'):
                    self._logger.info("Received search message: {}".format(msg.text))
                    await self._search_mode(msg)
                elif msg.is_reply and msg.start.startswith('/getid'):
                    self._logger.info("Received getid message: {}".format(msg.text))
                    await self._getid_mode(msg)

        self._set_file_handler('plus_mode')
        self._client.start()
        self._client.run_until_disconnected()