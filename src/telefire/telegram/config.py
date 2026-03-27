import os
from dataclasses import dataclass
from pathlib import Path

DEFAULT_SESSION_NAME = "telefire"


@dataclass(slots=True)
class TelegramRuntimeConfig:
    api_id: int
    api_hash: str
    session_name: str = DEFAULT_SESSION_NAME
    store_dir: Path = Path.home() / ".telefire" / "telegram"

    @classmethod
    def from_env(cls, session: str | None = None) -> "TelegramRuntimeConfig":
        api_id = (os.environ.get("TELEGRAM_API_ID") or "").strip()
        api_hash = (os.environ.get("TELEGRAM_API_HASH") or "").strip()
        if not api_id or not api_hash:
            raise ValueError(
                "Please set TELEGRAM_API_ID and TELEGRAM_API_HASH, or run: telefire init"
            )

        session_name = (
            session or os.environ.get("TELEGRAM_SESSION_NAME") or DEFAULT_SESSION_NAME
        ).strip() or DEFAULT_SESSION_NAME
        store_dir = Path(
            os.environ.get("TELEGRAM_STORE_DIR") or (Path.home() / ".telefire" / "telegram")
        )
        return cls(
            api_id=int(api_id),
            api_hash=api_hash,
            session_name=session_name,
            store_dir=store_dir,
        )
