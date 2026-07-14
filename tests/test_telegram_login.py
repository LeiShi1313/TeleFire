import stat
from pathlib import Path
from types import SimpleNamespace

import pytest

from telefire.plugins.base import command_registry
from telefire.plugins.telegram_login import TelegramLogin
import telefire.plugins.telegram_login as telegram_login


class FakeTelegramClient:
    instances = []
    fail_start = False

    def __init__(self, session_path, api_id, api_hash):
        self.session_path = Path(session_path)
        self.api_id = api_id
        self.api_hash = api_hash
        self.disconnected = False
        self.__class__.instances.append(self)

    async def start(self):
        if self.fail_start:
            raise RuntimeError("login failed")
        session_file = self.session_path.with_suffix(".session")
        session_file.write_text("test session")
        session_file.chmod(0o644)

    async def get_me(self):
        return SimpleNamespace(id=12345, bot=False)

    async def disconnect(self):
        self.disconnected = True


@pytest.fixture(autouse=True)
def reset_fake_client():
    FakeTelegramClient.instances = []
    FakeTelegramClient.fail_start = False


def test_login_command_creates_and_secures_the_requested_session(
    monkeypatch, tmp_path, capsys
):
    config = SimpleNamespace(
        api_id=123,
        api_hash="hash",
        session_name="ai_e2e_peer",
        store_dir=tmp_path,
    )
    requested = {}

    def from_account(cls, account=None, session=None):
        requested.update(account=account, session=session)
        return config

    monkeypatch.setattr(
        telegram_login.TelegramRuntimeConfig,
        "from_account",
        classmethod(from_account),
    )
    monkeypatch.setattr(telegram_login, "TelegramClient", FakeTelegramClient)

    TelegramLogin()(account="ai_e2e_peer")

    client = FakeTelegramClient.instances[0]
    session_file = tmp_path / "ai_e2e_peer.session"
    assert requested == {"account": "ai_e2e_peer", "session": None}
    assert client.session_path == tmp_path / "ai_e2e_peer"
    assert stat.S_IMODE(session_file.stat().st_mode) == 0o600
    assert client.disconnected is True
    assert "ai_e2e_peer" in capsys.readouterr().out


def test_login_command_disconnects_when_authorization_fails(monkeypatch, tmp_path):
    config = SimpleNamespace(
        api_id=123,
        api_hash="hash",
        session_name="ai_e2e_peer",
        store_dir=tmp_path,
    )
    monkeypatch.setattr(
        telegram_login.TelegramRuntimeConfig,
        "from_account",
        classmethod(lambda cls, account=None, session=None: config),
    )
    monkeypatch.setattr(telegram_login, "TelegramClient", FakeTelegramClient)
    FakeTelegramClient.fail_start = True

    with pytest.raises(RuntimeError, match="login failed"):
        TelegramLogin()(account="ai_e2e_peer")

    assert FakeTelegramClient.instances[0].disconnected is True


@pytest.mark.parametrize("session_name", ("", ".", "..", "../peer", "/tmp/peer"))
def test_login_command_rejects_unsafe_session_names(
    monkeypatch, tmp_path, session_name
):
    config = SimpleNamespace(
        api_id=123,
        api_hash="hash",
        session_name=session_name,
        store_dir=tmp_path,
    )
    monkeypatch.setattr(
        telegram_login.TelegramRuntimeConfig,
        "from_account",
        classmethod(lambda cls, account=None, session=None: config),
    )
    monkeypatch.setattr(telegram_login, "TelegramClient", FakeTelegramClient)

    with pytest.raises(ValueError, match="simple file name"):
        TelegramLogin()(account="ai_e2e_peer")

    assert FakeTelegramClient.instances == []


def test_login_command_is_registered_under_telegram():
    commands = command_registry.as_fire_commands()

    assert commands["telegram"]["login"]
