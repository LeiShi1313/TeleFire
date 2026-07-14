import asyncio
from pathlib import Path

from telethon import TelegramClient

from telefire.plugins.base import PluginMount
from telefire.telegram.config import TelegramRuntimeConfig


_SESSION_SUFFIXES = (".session", ".session-journal", ".session-shm", ".session-wal")


def _validate_session_name(session_name: str) -> None:
    path = Path(session_name)
    if (
        not session_name
        or session_name in {".", ".."}
        or path.is_absolute()
        or len(path.parts) != 1
    ):
        raise ValueError("Telegram session name must be a simple file name")


def _secure_session_files(session_path: Path) -> None:
    for suffix in _SESSION_SUFFIXES:
        candidate = session_path.with_name(f"{session_path.name}{suffix}")
        if candidate.exists():
            candidate.chmod(0o600)


class TelegramLogin(metaclass=PluginMount):
    command_group = "telegram"
    command_name = "login"

    def __call__(
        self,
        account: str = "default",
        session: str | None = None,
    ) -> None:
        """Interactively authorize a Telegram user session."""
        config = TelegramRuntimeConfig.from_account(account=account, session=session)
        _validate_session_name(config.session_name)
        asyncio.run(self._login(config))

    async def _login(self, config: TelegramRuntimeConfig) -> None:
        config.store_dir.mkdir(parents=True, exist_ok=True)
        session_path = config.store_dir / config.session_name

        # Fresh login must not copy a legacy session into the requested name.
        client = TelegramClient(str(session_path), config.api_id, config.api_hash)
        try:
            await client.start()
            identity = await client.get_me()
            account_type = "bot" if identity.bot else "user"
            print(
                f"Authorized Telegram session '{config.session_name}' "
                f"for {account_type} {identity.id}."
            )
        finally:
            try:
                await client.disconnect()
            finally:
                _secure_session_files(session_path)
