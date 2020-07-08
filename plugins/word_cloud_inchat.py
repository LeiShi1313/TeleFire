import re
import traceback
from io import BytesIO
from dateutil import parser
from datetime import timezone, datetime, timedelta

from telethon import utils
from telethon.sync import events
from telethon.tl.functions.channels import CreateChannelRequest
# from telethon.tl.types import 

from plugins.base import Telegram, PluginMount


class Action(Telegram, metaclass=PluginMount):
    command_name = "word_cloud_inchat"
    prefix = "word_cloud_inchat_"

    async def _generate_word_cloud_async(self, msg_id: str, reply_msg, chat, user, start: datetime, end: datetime):
        try:
            import jieba
            from wordcloud import WordCloud
        except ImportError as e:
            print(e)
            return
        words = []
        count = 0
        initial_msg = reply_msg.text + '\n'
        async for msg in self._client.iter_messages(chat, from_user=user, offset_date=end):
            if start and msg.date < start:
                break
            if msg.text:
                words += [w for w in jieba.cut_for_search(msg.text) if not await self.redis.sismember(f'{self.prefix}stop_words', w)]
                count += 1
            if count % 1000 == 1:
                initial_msg += '.'
                await reply_msg.edit(text=initial_msg)
        try:
            image = WordCloud(font_path="simsun.ttf", width=800, height=400).generate(' '.join(words)).to_image()
            stream = BytesIO()
            image.save(stream, 'PNG')
            await self._client.send_message(
                chat,
                'Wordcloud for\n{}{}{}'.format(
                    f'{chat.title}',
                    f'\n{utils.get_display_name(user)}' if user else '',
                    '\n{}-{}'.format(
                        start.strftime('%Y/%m/%d') if start else 'Join',
                        end.strftime('%Y/%m/%d') if end else 'Now') if start or end else ''),
                reply_to=msg_id,
                file=stream.getvalue())
        finally:
            traceback.print_exc()
            await reply_msg.delete()
            

    def __call__(self, redis):
        import aioredis
        @self._client.on(events.NewMessage)
        async def _inner(event):
            msg = event.message
            try:
                if msg.text and msg.text.startswith('wordcloud'):
                    chat = await event.get_chat()
                    user = None
                    if msg.is_reply:
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
                        f'\n{chat.title}',
                        f'\n{utils.get_display_name(user)}' if user else '',
                        '\n`{}-{}`'.format(
                            start.strftime('%Y/%m/%d') if start else 'Join',
                            end.strftime('%Y/%m/%d') if end else 'Now') if start or end else '')
                    self._logger.info(reply_words.replace('\n', ' '))
                    reply_msg = await self._client.send_message(chat, reply_words, reply_to=msg.id)
                    await self._generate_word_cloud_async(msg.id, reply_msg, chat, user, start, end)
            except Exception as e:
                traceback.print_exc()

        async def connect_redis():
            self.redis = await aioredis.create_redis_pool(f'redis://{redis}')
        
        with self._client:
            self._client.loop.run_until_complete(connect_redis())
        
        self._set_file_handler("word_cloud_inchat")
        self._logger.info("Wordcloud inchat mode start")
        self._client.start()
        self._client.run_until_disconnected()
