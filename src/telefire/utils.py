import re

import aiohttp
from telethon.tl.types import Channel, Message


def camel_to_snake(name: str):
    name = re.sub('(.)([A-Z][a-z]+)', r'\1_\2', name)
    return re.sub('([a-z0-9])([A-Z])', r'\1_\2', name).lower()


def get_url(channel: Channel, msg: Message):
    if channel.username:
        return "https://t.me/{}/{}".format(channel.username, msg.id)
    return "https://t.me/c/{}/{}".format(channel.id, msg.id)

async def send_to_pushbullet(access_token: str, device_iden: str, title: str, body: str, url: str):
    payload: dict = {
        'title': title,
        'body': body,
        'url': url,
        'type': 'note',
        'device_iden': device_iden
    }
    headers = {'Access-Token': access_token}
    async with aiohttp.ClientSession() as session:
        async with session.post('https://api.pushbullet.com/v2/pushes', json=payload, headers=headers) as resp:
            return resp.status, await resp.text()
