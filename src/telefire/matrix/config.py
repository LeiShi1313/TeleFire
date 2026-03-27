import os
from dataclasses import dataclass
from pathlib import Path

from telefire.config import read_config_file


@dataclass(slots=True)
class MatrixRuntimeConfig:
    account: str
    base_url: str
    user_id: str
    store_dir: Path
    device_name: str = "telefire"
    password: str | None = None
    access_token: str | None = None
    device_id: str | None = None

    @classmethod
    def from_account(cls, account: str | None = None) -> "MatrixRuntimeConfig":
        selected_account = (
            account or os.environ.get("MATRIX_ACCOUNT") or "default"
        ).strip() or "default"

        config = read_config_file()
        matrix = config.get("matrix", {})

        # Default account reads from [matrix] directly;
        # named accounts read from [matrix.<name>] sub-tables.
        if selected_account == "default":
            account_config = matrix
        else:
            account_config = matrix.get(selected_account)
            if not isinstance(account_config, dict):
                account_config = {}

        base_url = (os.environ.get("MATRIX_BASE_URL") or account_config.get("base_url", "")).strip().rstrip("/")
        user_id = (os.environ.get("MATRIX_USER_ID") or account_config.get("user_id", "")).strip()

        if not base_url or not user_id:
            raise ValueError(
                f"Please configure Matrix account '{selected_account}' or set MATRIX_BASE_URL and MATRIX_USER_ID"
            )

        password = (os.environ.get("MATRIX_PASSWORD") or account_config.get("password", "")).strip() or None
        access_token = (os.environ.get("MATRIX_ACCESS_TOKEN") or account_config.get("access_token", "")).strip() or None
        device_id = (os.environ.get("MATRIX_DEVICE_ID") or account_config.get("device_id", "")).strip() or None
        device_name = (
            os.environ.get("MATRIX_DEVICE_NAME") or account_config.get("device_name", "telefire")
        ).strip() or "telefire"
        default_store_dir = Path.home() / ".telefire" / "matrix" / selected_account
        store_dir = Path(
            os.environ.get("MATRIX_STORE_DIR") or account_config.get("store_dir", default_store_dir)
        )

        return cls(
            account=selected_account,
            base_url=base_url,
            user_id=user_id,
            store_dir=store_dir,
            device_name=device_name,
            password=password,
            access_token=access_token,
            device_id=device_id,
        )

    @property
    def session_path(self) -> Path:
        return self.store_dir / "session.json"

    @property
    def sync_store_path(self) -> Path:
        return self.store_dir / "sync_store.json"

    @property
    def state_store_path(self) -> Path:
        return self.store_dir / "state_store.bin"
