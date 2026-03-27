from io import BytesIO
from math import floor
from os.path import exists
from os import makedirs, remove
from PIL import Image, ImageFont, ImageDraw
from telethon import utils as telethon_utils
from telethon.sync import events
from telefire.plugins.base import Telegram, PluginMount
from telefire.utils import get_url


def font(path, size):
    return ImageFont.truetype(f'{path}ZhuZiAWan-2.ttc', size=size, encoding="utf-8")

def cut(obj, sec):
    return [obj[i:i + sec] for i in range(0, len(obj), sec)]

async def yv_lu_process_image(name, text, photo, path):
    if len(name) > 16:
        name = name[:16] + '...'
    text = cut(text, 17)
    # 用户不存在头像时
    if not photo:
        photo = Image.open(f'{path}p4.png')
        # 对图片写字
        draw = ImageDraw.Draw(photo)
        # 计算使用该字体占据的空间
        # 返回一个 tuple (width, height)
        # 分别代表这行字占据的宽和高
        text_width = font(path, 60).getsize(name[0])
        if name[0].isalpha():
            text_coordinate = int((photo.size[0] - text_width[0]) / 2), int((photo.size[1] - text_width[1]) / 2) - 10
        else:
            text_coordinate = int((photo.size[0] - text_width[0]) / 2), int((photo.size[1] - text_width[1]) / 2)
        draw.text(text_coordinate, name[0], (255, 110, 164), font(path, 60))
    else:
        photo = Image.open(f'{path}{photo}')
    # 读取图片
    img1, img2, img3, mask = Image.open(f'{path}p1.png'), Image.open(f'{path}p2.png'), \
                             Image.open(f'{path}p3.png'), Image.open(f'{path}mask.png')
    size1, size2, size3 = img1.size, img2.size, img3.size
    photo_size = photo.size
    mask_size = mask.size
    scale = photo_size[1] / mask_size[1]
    photo = photo.resize((int(photo_size[0] / scale), int(photo_size[1] / scale)), Image.LANCZOS)
    mask1 = Image.new('RGBA', mask_size)
    mask1.paste(photo, mask=mask)
    # 创建空图片
    result = Image.new(img1.mode, (size1[0], size1[1] + size2[1] * len(text) + size3[1]))

    # 读取粘贴位置
    loc1, loc3, loc4 = (0, 0), (0, size1[1] + size2[1] * len(text)), (6, size1[1] + size2[1] * len(text) - 23)

    # 对图片写字
    draw = ImageDraw.Draw(img1)
    draw.text((60, 10), name, (255, 110, 164), font(path, 18))
    for i in range(len(text)):
        temp = Image.open(f'{path}p2.png')
        draw = ImageDraw.Draw(temp)
        draw.text((60, 0), text[i], (255, 255, 255), font(path, 18))
        result.paste(temp, (0, size1[1] + size2[1] * i))

    # 粘贴图片
    result.paste(img1, loc1)
    result.paste(img3, loc3)
    result.paste(mask1, loc4)

    # 保存图片
    result.save(f'{path}result.png')

    file = BytesIO()
    file.name = "sticker.webp"

    image = await resize_image('plugins/yvlu/result.png', 512)
    try:
        image.save(file, "WEBP")
    except KeyError:
        return None
    file.seek(0)
    return file

async def resize_image(photo, num):
    image = Image.open(photo)
    maxsize = (num, num)
    if (image.width and image.height) < num:
        size1 = image.width
        size2 = image.height
        if image.width > image.height:
            scale = num / size1
            size1new = num
            size2new = size2 * scale
        else:
            scale = num / size2
            size1new = size1 * scale
            size2new = num
        size1new = floor(size1new)
        size2new = floor(size2new)
        size_new = (size1new, size2new)
        image = image.resize(size_new)
    else:
        image.thumbnail(maxsize)
    return image

class Action(Telegram, metaclass=PluginMount):
    command_name = "yvlu"

    async def _yvlu_async(self, chat, user, msg):
        chat = await self._get_entity(chat)
        user = await self._get_entity(user)

        name = user.first_name
        if user.last_name:
            name += f' {user.last_name}'

        if not exists(f'plugins/yvlu/{user.id}.jpg'):
            await self._client.download_profile_photo(user, f'plugins/yvlu/{user.id}.jpg')

        file = await yv_lu_process_image(name, msg, f'{user.id}.jpg', "plugins/yvlu/")
        await self._client.send_file(
            chat,
            file,
            force_document=False,
        )

    def __call__(self, chat, user, msg):
        self._run_command(self._yvlu_async(chat, user, msg))
