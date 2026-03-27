import os
from dataclasses import dataclass
from pathlib import Path

from telefire.config import read_config_file
from telefire.constants import DEFAULT_SESSION_NAME, DEFAULT_TELEGRAM_ACCOUNT


@dataclass(slots=True)
class TelegramRuntimeConfig:
    account: str
    api_id: int
    api_hash: str
    session_name: str = DEFAULT_SESSION_NAME
    store_dir: Path = Path.home() / ".telefire" / "telegram"

    @classmethod
    def from_account(
        cls,
        account: str | None = None,
        session: str | None = None,
    ) -> "TelegramRuntimeConfig":
        config = read_config_file()
        telegram = config.get("telegram", {})
        telegram_accounts = config.get("telegram_accounts", {})

        default_account = (
            os.environ.get("TELEGRAM_DEFAULT_ACCOUNT")
            or telegram.get("default_account")
            or DEFAULT_TELEGRAM_ACCOUNT
        ).strip() or DEFAULT_TELEGRAM_ACCOUNT
        selected_account = (
            account or os.environ.get("TELEGRAM_ACCOUNT") or default_account
        ).strip() or default_account

        api_id = (os.environ.get("TELEGRAM_API_ID") or str(telegram.get("api_id", ""))).strip()
        api_hash = (os.environ.get("TELEGRAM_API_HASH") or telegram.get("api_hash", "")).strip()
        if not api_id or not api_hash:
            raise ValueError(
                "Please set TELEGRAM_API_ID and TELEGRAM_API_HASH, or run: telefire init"
            )

        account_config = telegram_accounts.get(selected_account)
        if not isinstance(account_config, dict):
            account_config = {}

        legacy_session_name = ""
        if selected_account == default_account:
            legacy_session_name = telegram.get("session_name", "")

        session_name = (
            session
            or os.environ.get("TELEGRAM_SESSION_NAME")
            or account_config.get("session_name")
            or legacy_session_name
            or (
                DEFAULT_SESSION_NAME
                if selected_account == DEFAULT_TELEGRAM_ACCOUNT
                else selected_account
            )
        ).strip()
        store_dir = Path(
            os.environ.get("TELEGRAM_STORE_DIR")
            or telegram.get("store_dir", Path.home() / ".telefire" / "telegram")
        )
        return cls(
            account=selected_account,
            api_id=int(api_id),
            api_hash=api_hash,
            session_name=session_name,
            store_dir=store_dir,
        )

    @classmethod
    def from_env(cls, session: str | None = None) -> "TelegramRuntimeConfig":
        return cls.from_account(session=session)
