import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(slots=True)
class TelegramRuntimeConfig:
    api_id: str
    api_hash: str
    session_name: str = "test"
    store_dir: Path = Path.home() / ".telefire"

    @classmethod
    def from_env(cls, session: str = "test") -> "TelegramRuntimeConfig":
        api_id = (os.environ.get("TELEGRAM_API_ID") or "").strip()
        api_hash = (os.environ.get("TELEGRAM_API_HASH") or "").strip()
        if not api_id or not api_hash:
            raise ValueError(
                "Please set TELEGRAM_API_ID and TELEGRAM_API_HASH, or run: telefire init"
            )

        store_dir = Path(os.environ.get("TELEGRAM_STORE_DIR") or (Path.home() / ".telefire"))
        return cls(
            api_id=api_id,
            api_hash=api_hash,
            session_name=session,
            store_dir=store_dir,
        )

    @property
    def session_path(self) -> str:
        local_session = Path(f"{self.session_name}.session")
        if local_session.exists():
            return self.session_name

        self.store_dir.mkdir(parents=True, exist_ok=True)
        return str(self.store_dir / self.session_name)
