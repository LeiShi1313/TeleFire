import inspect
from collections.abc import Awaitable, Callable

from telethon import TelegramClient

from telefire.runtime import build_logger
from telefire.telegram.config import TelegramRuntimeConfig


async def _maybe_await(result):
    if inspect.isawaitable(result):
        return await result
    return result


class TelegramService:
    def __init__(self, config: TelegramRuntimeConfig, log_level: str = "info"):
        self.config = config
        self.logger = build_logger(__name__, log_level=log_level)
        self.client = TelegramClient(
            config.session_path,
            config.api_id,
            config.api_hash,
        )

    async def connect(self) -> TelegramClient:
        if not self.client.is_connected():
            await self.client.start()
        return self.client

    async def close(self) -> None:
        if self.client.is_connected():
            await self.client.disconnect()

    async def run_once(self, callback: Callable[["TelegramService"], Awaitable[object] | object]) -> object:
        await self.connect()
        try:
            return await _maybe_await(callback(self))
        finally:
            await self.close()

    async def run_forever(
        self,
        setup: Callable[["TelegramService"], Awaitable[object] | object] | None = None,
    ) -> None:
        await self.connect()
        try:
            if setup is not None:
                await _maybe_await(setup(self))
            await self.client.run_until_disconnected()
        finally:
            await self.close()
