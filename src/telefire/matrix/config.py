import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(slots=True)
class MatrixRuntimeConfig:
    base_url: str
    user_id: str
    store_dir: Path
    device_name: str = "telefire"
    password: str | None = None
    access_token: str | None = None
    device_id: str | None = None

    @classmethod
    def from_env(cls) -> "MatrixRuntimeConfig":
        base_url = (os.environ.get("MATRIX_BASE_URL") or "").strip().rstrip("/")
        user_id = (os.environ.get("MATRIX_USER_ID") or "").strip()

        if not base_url or not user_id:
            raise ValueError(
                "Please set MATRIX_BASE_URL and MATRIX_USER_ID, or run: telefire init"
            )

        password = (os.environ.get("MATRIX_PASSWORD") or "").strip() or None
        access_token = (os.environ.get("MATRIX_ACCESS_TOKEN") or "").strip() or None
        device_id = (os.environ.get("MATRIX_DEVICE_ID") or "").strip() or None
        device_name = (os.environ.get("MATRIX_DEVICE_NAME") or "telefire").strip() or "telefire"
        store_dir = Path(
            os.environ.get("MATRIX_STORE_DIR") or (Path.home() / ".telefire" / "matrix")
        )

        return cls(
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
