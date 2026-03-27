from telethon import TelegramClient

from telefire.runtime import build_logger
from telefire.telegram.config import TelegramRuntimeConfig
from telefire.telegram.store import TelegramSessionStore


class TelegramService:
    def __init__(self, config: TelegramRuntimeConfig, log_level: str = "info"):
        self.config = config
        self.logger = build_logger(__name__, log_level=log_level)
        self.session_store = TelegramSessionStore(
            config.store_dir,
            config.session_name,
        )
        self._client = TelegramClient(
            self.session_store.client_session_path,
            config.api_id,
            config.api_hash,
        )

    @property
    def client(self) -> TelegramClient:
        return self._client

    async def connect(self) -> TelegramClient:
        self.session_store.prepare()
        if not self.client.is_connected():
            await self.client.start()
        return self.client

    async def close(self) -> None:
        if self.client.is_connected():
            await self.client.disconnect()

    async def wait_until_disconnected(self) -> None:
        await self.client.run_until_disconnected()
