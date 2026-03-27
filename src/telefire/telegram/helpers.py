import re
from collections import Counter
from math import floor

from telethon import TelegramClient, utils
from telethon.hints import EntitiesLike
from telethon.tl.types import Channel, Message, User

from telefire.utils import get_url


class TelegramEntitiesHelper:
    def __init__(self, client: TelegramClient):
        self.client = client

    async def get(self, entity_like):
        try:
            return await self.client.get_entity(int(entity_like))
        except Exception:
            return await self.client.get_entity(entity_like)

    def same(self, entity: EntitiesLike, other):
        return (
            str(entity.id) == str(other)
            or str(entity.username) == str(other)
            or f"-100{entity.id}" == str(other)
            or utils.get_display_name(entity) == str(other)
        )

    def parse(self, msg, key, regex):
        match = re.search(rf"{re.escape(key)}=({regex})", msg)
        if match is not None:
            return match.groups()[0]
        return None

    def clean(self, msg, key):
        return re.sub(rf"{re.escape(key)}=([0-9a-zA-Z_\-]+)", "", msg)

    async def parse_from_text(self, msg: str, entity_name: str):
        value = self.parse(msg, entity_name, r"[0-9a-zA-Z_\-]+")
        if value is not None:
            return await self.get(value)
        return None


class TelegramMessagesHelper:
    def __init__(self, client: TelegramClient, logger, entities: TelegramEntitiesHelper):
        self.client = client
        self.logger = logger
        self.entities = entities

    def log(self, msg: Message, channel: Channel, user: User):
        self.logger.info("{}: {}".format(utils.get_display_name(user), msg.text))

    async def sender_name(self, msg: Message):
        sender = await msg.get_sender()
        if sender is None:
            if msg.post_author:
                return msg.post_author
            if msg.peer_id:
                return utils.get_display_name(msg.peer_id)
            return "Unknown"
        return utils.get_display_name(sender)

    async def iter(
        self,
        chat,
        user,
        query,
        output,
        print_stat=False,
        cut_func=None,
        offset_date=None,
        min_date=None,
    ):
        if print_stat:
            counter = Counter()
        async for msg in self.client.iter_messages(
            chat, from_user=user, offset_date=offset_date
        ):
            if min_date and msg.date and msg.date.replace(tzinfo=None) < min_date:
                break
            if not query or (msg.text and query in msg.text):
                if isinstance(output, Channel):
                    url = get_url(chat, msg)
                    await self.client.send_message(
                        output,
                        "{}:\n{}\n{}".format(
                            msg.date,
                            msg.text if cut_func is None else cut_func(msg.text),
                            url,
                        ),
                    )
                else:
                    sender = user
                    if sender is None:
                        if msg.post:
                            sender = chat
                        elif msg.from_id is None:
                            self.logger.debug(msg)
                            continue
                        else:
                            sender = await self.client.get_entity(msg.from_id)
                    self.log(msg, chat, sender)
                if print_stat:
                    counter[msg.date.hour] += 1

        if print_stat:
            total = sum(counter.values())
            for hour in range(24):
                print("{}: {}".format(hour, floor(counter[hour] / total * 100) * "="))


class TelegramHelpers:
    def __init__(self, client: TelegramClient, logger):
        self.entities = TelegramEntitiesHelper(client)
        self.messages = TelegramMessagesHelper(client, logger, self.entities)
