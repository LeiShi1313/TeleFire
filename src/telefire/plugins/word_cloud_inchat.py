import re
import math
import asyncio
import traceback
from io import BytesIO
from dateutil import parser
from datetime import timezone, datetime, timedelta
from collections import defaultdict

from telethon import utils
from telethon.sync import events
from telethon.tl.functions.channels import CreateChannelRequest

from telefire.plugins.base import PluginMount
from telefire.telegram import TelegramCommand
from telefire.storage import Storage


class Action(TelegramCommand, metaclass=PluginMount):
    command_name = "wordcloud"
    prefix = "wordcloud_"

    async def _generate_word_cloud_async(self, msg_id, reply_msg, to_chat, search_chat, user, start, end):
        try:
            import jieba
            from wordcloud import WordCloud
        except ImportError as e:
            print(e)
            return
        words = defaultdict(int)
        count = 0
        initial_msg = reply_msg.text + '\n'
        async for msg in self.client.iter_messages(search_chat, from_user=user, offset_date=end):
            if start and msg.date < start:
                break
            if msg.text:
                for word in jieba.cut(msg.text):
                    word = word.lower()
                    if not await self.store.sismember(f'{self.prefix}stop_words', word):
                        words[word] += 1

            count += 1
            if count >= 1000:
                p = math.floor(math.log(count, 10))
                if count % int(math.pow(10, p)) == 0 and count // 1000:
                    try:
                        await reply_msg.edit(text=initial_msg + '.' * (count // 1000))
                    except Exception as _:
                        traceback.print_exc()

        wordcloud_msg = None
        try:
            image = WordCloud(font_path="simsun.ttf", width=800, height=400, background_color=(4, 57, 39)).generate_from_frequencies(words).to_image()
            stream = BytesIO()
            image.save(stream, 'PNG')
            wordcloud_msg = await self.client.send_message(
                to_chat,
                '词云 for\n{}{}{}'.format(
                    f'{search_chat.title}',
                    f'\n{utils.get_display_name(user)}' if user else '',
                    '\n{}-{}'.format(
                        start.strftime('%Y/%m/%d') if start else 'Join',
                        end.strftime('%Y/%m/%d') if end else 'Now') if start or end else ''),
                reply_to=msg_id,
                file=stream.getvalue())
        except Exception as _:
            traceback.print_exc()
        finally:
            await reply_msg.delete()


    def __call__(self, db=None):
        self.store = Storage(db)

        async def setup():
            await self.store.connect()
            # Load stop words into storage
            try:
                with open('StopWords.txt', 'r') as f:
                    for word in f.readlines():
                        word = word.strip()
                        await self.store.sadd(f"{self.prefix}stop_words", word)
            except FileNotFoundError:
                pass

            @self.client.on(events.NewMessage)
            async def _inner(event):
                msg = event.message
                try:
                    if msg.text and msg.text.lower().startswith('wordcloud'):
                        to_chat = await event.get_chat()
                        search_chat = to_chat
                        m = re.search(r'chat=(?P<chat>[0-9a-zA-Z_\-]+)', msg.text)
                        if m is not None:
                            search_chat = await self.helpers.entities.get(m.groupdict().get('chat'))

                        user = None
                        m = re.search(r'user=(?P<user>[0-9a-zA-Z_\-]+)', msg.text)
                        if m is not None:
                            user = await self.helpers.entities.get(m.groupdict().get('user'))
                        elif msg.is_reply:
                            reply_to = await msg.get_reply_message()
                            user = await reply_to.get_sender()
                        elif 'group' not in msg.text:
                                user = await msg.get_sender()

                        start, end = None, msg.date
                        m = re.search(r'end=(?P<end>[0-9_\-/: ]+)', msg.text)
                        if m is not None:
                            end = parser.parse(m.groupdict().get('end'))
                        m = re.search(r'start=(?P<start>[0-9_\-/: ]+)', msg.text)
                        if m is not None:
                            start = parser.parse(m.groupdict().get('start')).replace(tzinfo=timezone.utc)
                        else:
                            start = end - timedelta(days=1)
                        reply_words = 'Received wordcloud request for {}{}{}'.format(
                            f'\n{search_chat.title}',
                            f'\n{utils.get_display_name(user)}' if user else '',
                            '\n`{}-{}`'.format(
                                start.strftime('%Y/%m/%d') if start else 'Join',
                                end.strftime('%Y/%m/%d') if end else 'Now') if start or end else '')
                        self.logger.info(reply_words.replace('\n', ' '))
                        reply_msg = await self.client.send_message(to_chat, reply_words, reply_to=msg.id)
                        await self._generate_word_cloud_async(msg.id, reply_msg, to_chat, search_chat, user, start, end)
                except Exception:
                    traceback.print_exc()

        self.set_file_handler("word_cloud_inchat")
        self.logger.info("Wordcloud inchat mode start")
        self.run_forever(setup=setup)
