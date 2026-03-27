from io import BytesIO
from datetime import timezone
from dateutil import parser
from telethon import utils
from telethon.tl.functions.channels import CreateChannelRequest

from telefire.plugins.base import PluginMount
from telefire.telegram import TelegramCommand

stop_words = {"www", "com", "https", "http", "htm", "html", "还是", "就是", "这是", "以前", "不过", "不行", "这个", "那个", "什么", "感觉", "现在", "可以", "不是", "知道", "好像", "但是", "还有", "怎么", "然后", "你们", "我们", "时候", "没有", "自己", "一个", "这么", "觉得", "而且", "这种", "已经", "不到", "开始", "很多", "这样", "可能", "有点", "之前", "以后", "最近", "所以", "应该", "里面", "不会", "出来", "真的", "只有", "地方", "真的", "看到", "不了", "那么", "不用", "主要", "反正", "其实", "的话", "起来", "肯定", "如果", "只是", "问题", "事情", "估计", "直接", "因为", "不能", "需要", "一样", "确实", "不然", "估计", "发现", "大家", "今天", "或者", "这边", "为了", "本来", "东西", "是不是", "不要", "基本", "一般", "多少", "以下", "一下", "有些", "有人", "他们", "这次", "其他", "只能", "这些", "看看", "那种", "说", "没", "做"}

class Action(TelegramCommand, metaclass=PluginMount):
    command_name = "word_cloud"

    async def _generate_word_cloud_async(self, chat, user, start, end):
        try:
            import jieba
            from wordcloud import WordCloud
        except ImportError as e:
            print(e)
            return
        
        chat = await self.helpers.entities.get(chat)
        if user is not None:
            user = await self.helpers.entities.get(user)
        if start is not None:
            start = parser.parse(start).replace(tzinfo=timezone.utc)
        if end is not None:
            end = parser.parse(end)
        words = []
        async for msg in self.client.iter_messages(chat, from_user=user, offset_date=end):
            if start and msg.date < start:
                break
            if msg.text:
                print("[{}][{}] {}".format(
                    msg.date,
                    utils.get_display_name(await msg.get_sender()) if user is None else utils.get_display_name(user),
                    msg.text))
                words += [w for w in jieba.cut_for_search(msg.text) if w not in stop_words]
        image = WordCloud(font_path="simsun.ttf", width=800, height=400).generate(' '.join(words)).to_image()
        stream = BytesIO()
        image.save(stream, 'PNG')
        await self.client.send_message(
                'gua_mei_debug',
                '{}{}{}'.format(
                    f'{chat.title}',
                    f'\n{utils.get_display_name(user)}' if user else '',
                    '\n{}-{}'.format(
                        start.strftime('%Y/%m/%d') if start else 'Join',
                        end.strftime('%Y/%m/%d') if end else 'Now') if start or end else ''),
                file=stream.getvalue())
            

    def __call__(self, chat: str, user=None, start=None, end=None):
        self.run_once(lambda: self._generate_word_cloud_async(chat, user, start, end))
