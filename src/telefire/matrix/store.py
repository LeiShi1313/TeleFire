import json
import os
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path

from mautrix.client.state_store import SyncStore
from mautrix.types import SyncToken


def _atomic_write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent, delete=False
    ) as handle:
        json.dump(data, handle, indent=2, sort_keys=True)
        handle.write("\n")
        temp_path = Path(handle.name)

    os.chmod(temp_path, 0o600)
    os.replace(temp_path, path)


@dataclass(slots=True)
class MatrixSession:
    base_url: str
    user_id: str
    device_id: str
    access_token: str


class MatrixSessionStore:
    def __init__(self, path: Path):
        self.path = path

    def load(self) -> MatrixSession | None:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except (json.JSONDecodeError, OSError, UnicodeDecodeError):
            return None

        try:
            return MatrixSession(
                base_url=str(data["base_url"]).rstrip("/"),
                user_id=str(data["user_id"]),
                device_id=str(data["device_id"]),
                access_token=str(data["access_token"]),
            )
        except KeyError:
            return None

    def save(self, session: MatrixSession) -> None:
        _atomic_write_json(self.path, asdict(session))

    def clear(self) -> None:
        self.path.unlink(missing_ok=True)


class FileSyncStore(SyncStore):
    def __init__(self, path: Path):
        self.path = path
        self._next_batch: SyncToken | None = None

    async def open(self) -> None:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            self._next_batch = None
            return
        except (json.JSONDecodeError, OSError, UnicodeDecodeError):
            self._next_batch = None
            return

        next_batch = data.get("next_batch")
        self._next_batch = str(next_batch) if next_batch else None

    async def flush(self) -> None:
        if self._next_batch is None:
            self.path.unlink(missing_ok=True)
            return
        _atomic_write_json(self.path, {"next_batch": self._next_batch})

    async def put_next_batch(self, next_batch: SyncToken) -> None:
        self._next_batch = next_batch
        await self.flush()

    async def get_next_batch(self) -> SyncToken:
        return self._next_batch
