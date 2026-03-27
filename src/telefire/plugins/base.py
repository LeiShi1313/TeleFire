import re
import logging
import asyncio
import inspect
from functools import wraps
from pathlib import Path
from math import floor
from datetime import datetime
from collections import Counter

import aiohttp
from telethon import utils
from telethon.hints import EntitiesLike
from telethon.tl.types import Channel, Message, User

from telefire.runtime import build_logger
from telefire.telegram import TelegramRuntimeConfig, TelegramService
from telefire.utils import get_url, camel_to_snake


class Telegram(object):
    def __init__(self, session='test', log_level='info'):
        self._telegram = TelegramService(
            TelegramRuntimeConfig.from_env(session=session),
            log_level=log_level,
        )
        self._client = self._telegram.client
        self._logger = self._telegram.logger
        self._logFormatter = logging.Formatter("%(message)s")

    def _set_file_handler(self, method, channel=None, user=None, query=None):
        path = Path('logs').joinpath(method)
        if channel:
            path = path.joinpath(channel.title)
        if user:
            path = path.joinpath(utils.get_display_name(user))
        path.mkdir(parents=True, exist_ok=True)
        path = path.joinpath(f'{datetime.utcnow().strftime("%Y-%m-%d")}_[query={query if query else None}].log')
        file_handler = logging.FileHandler(path.absolute())
        file_handler.setFormatter(self._logFormatter)
        self._logger.addHandler(file_handler)

    def _log_message(self, msg: Message, channel: Channel, user: User):
        self._logger.info("{}: {}".format(
            # msg.date,
            # get_url(channel, msg),
            utils.get_display_name(user),
            msg.text))

    async def _send_to_ifttt_async(self, event, key, header, body, url):
        payload = {'value1': header, 'value2': body, 'value3': url}
        u = 'https://maker.ifttt.com/trigger/{}/with/key/{}'.format(event, key)
        async with aiohttp.ClientSession() as session:
            async with session.post(u, data=payload) as resp:
                self._logger.info("[{}] {}{}\nIFTTT status: {}".format(url, header, body, resp.status))

    async def _iter_messages_async(self, chat, user, query, output, print_stat=False, cut_func=None, offset_date=None, min_date=None):
        if print_stat:
            counter = Counter()
        async for msg in self._client.iter_messages(chat, from_user=user, offset_date=offset_date):
            if min_date and msg.date and msg.date.replace(tzinfo=None) < min_date:
                break
            if not query or (msg.text and query in msg.text):
                if isinstance(output, Channel):
                    url = get_url(chat, msg)
                    await self._client.send_message(output, "{}:\n{}\n{}".format(msg.date, msg.text if cut_func is None else cut_func(msg.text), url))
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

    async def _get_entity(self, entity_like):
        try:
            entity = await self._client.get_entity(int(entity_like))
        except Exception as e:
            entity = await self._client.get_entity(entity_like)
        return entity

    def _is_same_entity(self, entity: EntitiesLike, other):
        return str(entity.id) == str(other) or str(entity.username) == str(other) or f"-100{entity.id}" == str(other) or utils.get_display_name(entity) == str(other)

    async def _get_sender(self, msg: Message):
        sender = await msg.get_sender()
        if sender is None:
            if msg.post_author:
                return msg.post_author
            elif msg.peer_id:
                return utils.get_display_name(msg.peer_id)
            else:
                return 'Unknown'
        return utils.get_display_name(sender)

    def _parse_msg(self, msg, key, regex):
        m = re.search(r'{}=({})'.format(key, regex), msg)
        if m is not None:
            return m.groups()[0]
        return None

    def _clean_entity(self, msg, key):
        return re.sub(r'{}=({})'.format(key, r'[0-9a-zA-Z_\-]+'), '', msg)

    async def _parse_entity(self, msg: str, entity_name: str):
        m = self._parse_msg(msg, entity_name, r'[0-9a-zA-Z_\-]+')
        if m is not None:
            return await self._get_entity(m)
        return None

    async def _invoke_async(self, action):
        result = action() if callable(action) else action
        if inspect.isawaitable(result):
            return await result
        return result

    def _run_command(self, action):
        return asyncio.run(self._telegram.run_once(lambda _: self._invoke_async(action)))

    def _run_forever_command(self, setup=None):
        async def _setup(_):
            if setup is None:
                return None
            return await self._invoke_async(setup)

        return asyncio.run(self._telegram.run_forever(setup=_setup if setup else None))

class Commands(object):
    pass


def _iter_command_parameters(signature):
    for index, parameter in enumerate(signature.parameters.values()):
        if index == 0 and parameter.name == "self":
            continue
        yield parameter


def _build_command_signature(cls):
    call_signature = inspect.signature(cls.__call__)
    init_signature = inspect.signature(cls.__init__)

    call_parameters = list(_iter_command_parameters(call_signature))
    call_parameter_names = {parameter.name for parameter in call_parameters}

    init_parameters = []
    for parameter in _iter_command_parameters(init_signature):
        if parameter.kind in (
            inspect.Parameter.VAR_POSITIONAL,
            inspect.Parameter.VAR_KEYWORD,
        ):
            continue
        if parameter.name in call_parameter_names:
            continue
        init_parameters.append(
            parameter.replace(kind=inspect.Parameter.KEYWORD_ONLY)
        )

    return inspect.Signature(
        parameters=[*call_parameters, *init_parameters],
        return_annotation=call_signature.return_annotation,
    )


def _build_command_wrapper(cls):
    signature = _build_command_signature(cls)
    init_signature = inspect.signature(cls.__init__)
    call_parameters = list(_iter_command_parameters(inspect.signature(cls.__call__)))
    init_parameter_names = {
        parameter.name
        for parameter in _iter_command_parameters(init_signature)
        if parameter.kind
        not in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD)
    }

    @wraps(cls.__call__)
    def command(*args, **kwargs):
        bound = signature.bind(*args, **kwargs)
        bound.apply_defaults()

        command_values = dict(bound.arguments)
        init_kwargs = {
            name: command_values.pop(name)
            for name in list(command_values)
            if name in init_parameter_names
        }

        call_args = []
        call_kwargs = {}
        for parameter in call_parameters:
            if parameter.name not in command_values:
                continue
            value = command_values[parameter.name]
            if parameter.kind in (
                inspect.Parameter.POSITIONAL_ONLY,
                inspect.Parameter.POSITIONAL_OR_KEYWORD,
            ):
                call_args.append(value)
            elif parameter.kind == inspect.Parameter.VAR_POSITIONAL:
                call_args.extend(value)
            elif parameter.kind == inspect.Parameter.KEYWORD_ONLY:
                call_kwargs[parameter.name] = value
            elif parameter.kind == inspect.Parameter.VAR_KEYWORD:
                call_kwargs.update(value)

        return cls(**init_kwargs)(*call_args, **call_kwargs)

    command_name = cls.command_name if hasattr(cls, "command_name") else camel_to_snake(cls.__name__)
    command.__name__ = command_name
    command.__qualname__ = command.__name__
    command.__doc__ = inspect.getdoc(cls.__call__) or inspect.getdoc(cls)
    command.__signature__ = signature
    return command


class PluginMount(type):
    def __init__(cls, name, bases, attrs):
        super(PluginMount, cls).__init__(name, bases, attrs)
        command_name = cls.command_name if hasattr(cls, 'command_name') else camel_to_snake(cls.__name__)
        setattr(Commands, command_name, staticmethod(_build_command_wrapper(cls)))
