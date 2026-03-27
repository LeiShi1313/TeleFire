import shutil
from pathlib import Path


class TelegramSessionStore:
    _MIGRATABLE_SUFFIXES = (".session", ".session-journal", ".session-shm", ".session-wal")
    _LEGACY_SESSION_NAMES = ("test",)

    def __init__(self, store_dir: Path, session_name: str):
        self.store_dir = store_dir
        self.session_name = session_name

    @property
    def base_path(self) -> Path:
        return self.store_dir / self.session_name

    @property
    def client_session_path(self) -> str:
        self.prepare()
        return str(self.base_path)

    def prepare(self) -> None:
        self.store_dir.mkdir(parents=True, exist_ok=True)
        self._migrate_session_files(self.session_name, Path.cwd())
        self._migrate_session_files(self.session_name, self.store_dir.parent)
        for legacy_name in self._LEGACY_SESSION_NAMES:
            if legacy_name == self.session_name:
                continue
            self._migrate_session_files(legacy_name, self.store_dir)
            self._migrate_session_files(legacy_name, Path.cwd())
            self._migrate_session_files(legacy_name, self.store_dir.parent)

    def _migrate_session_files(self, source_name: str, source_dir: Path) -> None:
        for suffix in self._MIGRATABLE_SUFFIXES:
            source = source_dir / f"{source_name}{suffix}"
            target = self.store_dir / f"{self.session_name}{suffix}"
            if source.exists() and not target.exists():
                shutil.copy2(source, target)
