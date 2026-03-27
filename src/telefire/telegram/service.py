from telethon import TelegramClient

from telefire.runtime import build_logger
from telefire.telegram.config import TelegramRuntimeConfig


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

    async def wait_until_disconnected(self) -> None:
        await self.client.run_until_disconnected()
