import re
import aiohttp
from telethon.tl.types import Channel, Message


def camel_to_snake(name: str):
    name = re.sub('(.)([A-Z][a-z]+)', r'\1_\2', name)
    return re.sub('([a-z0-9])([A-Z])', r'\1_\2', name).lower()


def get_url(channel: Channel, msg: Message):
    if channel.username:
        return "https://t.me/{}/{}".format(channel.username, msg.id)
    else:
        return "https://t.me/c/{}/{}".format(channel.id, msg.id)


async def _send_to_ifttt_async(event: str, key: str, header, body, url):
    payload = {'value1': header, 'value2': body, 'value3': url}
    u = 'https://maker.ifttt.com/trigger/{}/with/key/{}'.format(event, key)
    async with aiohttp.ClientSession() as session:
        async with session.post(u, data=payload) as resp:
            self._logger.info("[{}] {}{}\nIFTTT status: {}".format(url, header, body, resp.status))

async def send_to_pushbullet(access_token: str, device_iden: str, title: str, body: str, url: str):
    url: str = 'https://api.pushbullet.com/v2/pushes'
    payload: dict = {
        'title': title,
        'body': body,
        'url': url,
        'type': 'note',
        'device_iden': device_iden
    }
    headers = {'Access-Token': access_token}
    print(payload, headers)
    async with aiohttp.ClientSession() as session:
        async with session.post(url, json=payload, headers=headers) as resp:
            return resp