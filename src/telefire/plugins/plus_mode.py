import re
import asyncio
import traceback
from g4f.client import Client
from os.path import exists
from datetime import timedelta

from telethon import utils
from telethon.sync import events
from telethon.tl.functions.channels import CreateChannelRequest

from telefire.plugins.base import Telegram, PluginMount
from telefire.plugins.yvlu import yv_lu_process_image


class PlusMode(Telegram, metaclass=PluginMount):
    command_name = "plus_mode"

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
        await msg.edit(text="[DELETED MESSAGE]")
        await asyncio.sleep(60)
        await msg.delete()

    def _parse_auto_delete_message(self, text):
        m = re.search(r"^\/([\d]+[s|m|h|d]) (.*)$", text, re.DOTALL)
        if m:
            num = int(m.group(1)[:-1]) if m.group(1)[:-1].isnumeric() else None
            net = m.group(1)[-1]
            if num is not None:
                if net == "d":
                    num *= 86400
                elif net == "h":
                    num *= 3600
                elif net == "m":
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
            await self._client.edit_message(msg, msg.text[4:], parse_mode="markdown")
        except Exception as e:
            self._logger.info(e)

    async def _yvlu_mode(self, msg):
        if not msg.reply_to:
            return
        reply = await msg.get_reply_message()
        reply_sender = await reply.get_sender()
        text = msg.text[6:]
        await msg.delete()
        name = utils.get_display_name(reply_sender)
        if not exists(f"plugins/yvlu/{reply_sender.id}.jpg"):
            await self._client.download_profile_photo(
                reply_sender, f"plugins/yvlu/{reply_sender.id}.jpg"
            )

        file = await yv_lu_process_image(
            name, text, f"{reply_sender.id}.jpg", "plugins/yvlu/"
        )
        await self._client.send_file(
            msg.chat_id, file, force_document=False, reply_to=reply
        )

    async def _cool_mode(self, msg):
        if not msg.out:
            return
        i = 31
        msgs = [msg.text[6:].replace("*", "")]
        msgs.append(msg.text[6:].replace("*", "**"))
        msgs.append(msg.text[6:].replace("*", "__"))
        msgs.append(msg.text[6:].replace("*", "~~"))
        try:
            while i:
                await asyncio.sleep(0.7)
                await self._client.edit_message(msg, msgs[i % 2], parse_mode="markdown")
                i -= 1
        except Exception as e:
            self._logger.info(e)

    async def _anti_porn(self, msg, to_chat):
        self._client.send_message(to_chat, "侦测到黄图哥")
        for i in range(10):
            await self._client.send_message(
                to_chat,
                "卍卍卍卍卍卍卍卍卍卍\n卍卍卍卍卍卍卍卍卍卍\n卍卍卍弹幕护体卍卍卍\n卍卍卍弹幕护体卍卍卍\n卍卍卍弹幕护体卍卍卍\n卍卍卍卍卍卍卍卍卍卍\n卍卍卍卍卍卍卍卍卍卍\n",
            )

    async def _getid_mode(self, msg):
        try:
            await msg.delete()
            me = self._client.get_me()
            sender = await msg.get_sender()
            await self._client.send_message(
                me, f"id: `{sender.id}`\nusername: `{sender.username}`"
            )
        except Exception as _:
            traceback.print_exc()

    async def _search_mode(self, evt):
        msg = evt.message
        if not msg.out:
            return
        try:
            chat = await self._parse_entity(msg.text, "chat")
            if not chat:
                chat = await evt.get_chat()
            user = await self._parse_entity(msg.text, "user")
            query = msg.text.split(" ")[-1]

            result = await self._client(
                CreateChannelRequest(
                    "{}{}".format(
                        "{} in ".format(utils.get_display_name(user)) if user else "",
                        chat.title,
                    ),
                    "query={}".format(query),
                )
            )

            created_channel = result.chats[0]
            self._logger.info("Channel: {} created.".format(created_channel.id))
            await self._iter_messages_async(chat, user, query, created_channel)
        except:
            traceback.print_exc()
        finally:
            await msg.delete()

    async def _summary_mode(self, evt):
        msg = evt.message
        if not msg.out:
            return
        try:
            user = await self._parse_entity(msg.text, "user")
            text = self._clean_entity(msg.text, 'user')
            _, count, *prompt = text.split(" ", 2)
            count = int(count)
            prompt = prompt[0] if prompt else "请用中文简洁地告诉我大家都在聊什么，涉及到具体话题时请提及讨论者的名字"
            chat = await evt.get_chat()
            reply = await msg.get_reply_message()
            if reply:
                user = await reply.get_sender()
            msgs = []
            async for msg in self._client.iter_messages(chat, from_user=user):
                if msg.text and count > 0:
                    sender = await msg.get_sender()
                    msgs.append(f"{utils.get_display_name(sender)}: {msg.text}")
                    count -= 1
                if count <= 0:
                    break
            prompt = prompt + "\n" + "\n".join(msgs[::-1])

            self._logger.info(f"Prompt: {prompt}")
            client = Client()
            completion = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {
                        "role": "system",
                        "content": "You are a helpful assistant which take what I provided and summerize it",
                    },
                    {
                        "role": "user",
                        "content": prompt,
                    },
                ],
            )
            self._logger.info(completion)
            await evt.message.edit( text=completion.choices[0].message.content.encode("utf8").decode())
        except:
            traceback.print_exc()

    async def _ask_mode(self, evt):
        msg = evt.message
        if not msg.out:
            return
        try:
            if msg.is_reply:
                reply = await msg.get_reply_message()
                prompt = reply.text
            else:
                prompt = msg.text.split(" ", 1)[1]

            self._logger.info(f"Prompt: {prompt}")
            completion = openai.ChatCompletion.create(
                model="gpt-3.5-turbo",
                messages=[
                    {
                        "role": "system",
                        "content": "You are a helpful assistant",
                    },
                    {
                        "role": "user",
                        "content": prompt,
                    },
                ],
            )
            self._logger.info(completion)
            await evt.message.edit(
                text=completion["choices"][0]
                .get("message")
                .get("content")
                .encode("utf8")
                .decode()
            )
        except:
            traceback.print_exc()

    async def _paolu_mode(self, chat, msg):
        try:
            me = await self._client.get_me()

            c = 0
            s = "防撤回你妹"
            async for _msg in self._client.iter_messages(chat, from_user=me):
                # await self._client.edit_message(_msg, s[c % len(s)])
                await _msg.delete()
                c += 1
        except:
            traceback.print_exc()
        finally:
            await msg.delete()

    async def _anti_yvlu(self, msg):
        sender = await msg.get_sender()
        if msg.reply_to_msg_id is not None and msg.text == "-yvlu":
            reply = await msg.get_reply_message()
            reply_sender = await reply.get_sender()
            if reply_sender.id == self.me.id:
                if reply.fwd_from:
                    await reply.delete()
                else:
                    text = reply.text
                    await reply.edit("PagerMaid爬")
                    await asyncio.sleep(10)
                    await reply.edit(text)

    def __call__(self):
        @self._client.on(events.NewMessage(incoming=True))
        async def _incoming(evt):
            msg = evt.message
            if msg.text:
                if msg.text == "-yvlu":
                    self._logger.info("Receied PagerMaid yvlu message")
                    await self._anti_yvlu(msg)

            to_chat = await evt.get_chat()
            sender = await msg.get_sender()

        @self._client.on(events.NewMessage(outgoing=True))
        async def _inner(evt):
            try:
                msg = evt.message
                print(evt)
                print(msg)
                if msg.text:
                    if re.search(r"^\/([\d]+[s|m|h|d]) (.*)$", msg.text, re.DOTALL):
                        self._logger.info(
                            "Received auto delete message: {}".format(msg.text)
                        )
                        await self._auto_delete_mode(evt, msg)
                    elif msg.text.startswith("/md"):
                        self._logger.info(
                            "Received markdown mode message: {}".format(msg.text)
                        )
                        await self._markdown_mode(msg)
                    elif msg.text.startswith("/yvlu"):
                        self._logger.info("Received yvlu message: {}".format(msg.text))
                        await self._yvlu_mode(msg)
                    elif msg.text.startswith("/cool"):
                        self._logger.info("Received cool message: {}".format(msg.text))
                        await self._cool_mode(msg)
                    elif msg.text.startswith("/search"):
                        self._logger.info(
                            "Received search message: {}".format(msg.text)
                        )
                        await self._search_mode(evt)
                    elif msg.text.startswith("/summary"):
                        self._logger.info(
                            "Received summary message: {}".format(msg.text)
                        )
                        await self._summary_mode(evt)
                    elif msg.text.startswith("/ask"):
                        self._logger.info(
                            "Received ask message: {}".format(msg.text)
                        )
                        await self._ask_mode(evt)
                    elif msg.text == "-paolu":
                        chat = await evt.get_chat()
                        self._logger.info("Received paolu message: {}".format(msg.text))
                        await self._paolu_mode(chat, msg)
                    elif msg.is_reply and msg.text.startswith("/getid"):
                        self._logger.info("Received getid message: {}".format(msg.text))
                        await self._getid_mode(msg)
            except:
                traceback.print_exc()

        async def prepare():
            self.me = await self._client.get_me()
            self._logger.info(f"Plus mode is running as {self.me.username}")

        self._set_file_handler("plus_mode")
        with self._client:
            self._client.loop.run_until_complete(prepare())
        self._client.start()
        self._client.run_until_disconnected()
