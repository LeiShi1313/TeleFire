"""
Configuration management for telefire.
Loads from ~/.telefire/config.toml with environment variable overrides.
"""
import os
from pathlib import Path

import tomllib
from telefire.constants import DEFAULT_SESSION_NAME, DEFAULT_TELEGRAM_ACCOUNT

CONFIG_DIR = Path.home() / ".telefire"
CONFIG_FILE = CONFIG_DIR / "config.toml"
DEFAULT_MATRIX_ACCOUNT = "default"
_CLI_ENV_BLOCKLIST = {
    "MATRIX_ACCESS_TOKEN",
    "MATRIX_BASE_URL",
    "MATRIX_DEVICE_ID",
    "MATRIX_DEVICE_NAME",
    "MATRIX_PASSWORD",
    "MATRIX_STORE_DIR",
    "MATRIX_USER_ID",
    "TELEGRAM_ACCOUNT",
    "TELEGRAM_SESSION_NAME",
    "TELEGRAM_STORE_DIR",
}


def read_config_file() -> dict:
    """Read ~/.telefire/config.toml and return raw nested config."""
    config: dict = {}
    if CONFIG_FILE.exists():
        with open(CONFIG_FILE, "rb") as f:
            config = tomllib.load(f)
    return config


def load_config() -> dict:
    """Load startup env overrides from ~/.telefire/config.toml for CLI bootstrap."""
    config = read_config_file()

    telegram = config.get("telegram", {})

    return {
        "TELEGRAM_API_ID": os.environ.get("TELEGRAM_API_ID") or str(telegram.get("api_id", "")),
        "TELEGRAM_API_HASH": os.environ.get("TELEGRAM_API_HASH") or telegram.get("api_hash", ""),
    }


def apply_config():
    """Load CLI startup env.

    Priority: existing env > config.toml API settings > .env file.
    Telegram and Matrix account settings are resolved later by runtime config.
    """
    # Load .env as lowest-priority fallback
    env_file = Path.cwd() / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, value = line.partition("=")
                key, value = key.strip(), value.strip().strip('"').strip("'")
                if key in _CLI_ENV_BLOCKLIST:
                    continue
                if key and value and not os.environ.get(key):
                    os.environ[key] = value

    for key, value in load_config().items():
        if value and not os.environ.get(key):
            os.environ[key] = value


def init_config():
    """Interactive first-run setup. Saves credentials to ~/.telefire/config.toml."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)

    existing = read_config_file()

    print("Telefire Setup")
    print("=" * 40)
    print(f"Config file: {CONFIG_FILE}\n")

    # Telegram
    tg = existing.get("telegram", {})
    telegram_accounts = {
        str(name): values
        for name, values in existing.get("telegram_accounts", {}).items()
        if isinstance(values, dict)
    }
    telegram_default_account = str(
        tg.get("default_account", DEFAULT_TELEGRAM_ACCOUNT)
    ) or DEFAULT_TELEGRAM_ACCOUNT
    if telegram_default_account not in telegram_accounts:
        telegram_accounts[telegram_default_account] = {
            "session_name": tg.get("session_name", DEFAULT_SESSION_NAME) or DEFAULT_SESSION_NAME,
        }

    print("[Telegram] (required)")
    api_id = input(f"  API ID [{tg.get('api_id', '')}]: ").strip()
    api_hash = input(f"  API Hash [{tg.get('api_hash', '')}]: ").strip()
    telegram_account = input(f"  Account [{telegram_default_account}]: ").strip()
    telegram_account = telegram_account or telegram_default_account
    telegram_account_config = telegram_accounts.get(telegram_account, {})
    session_name = input(
        f"  Session Name [{telegram_account_config.get('session_name', DEFAULT_SESSION_NAME)}]: "
    ).strip()
    telegram_store_dir = input(
        f"  Store Dir [{tg.get('store_dir', str(CONFIG_DIR / 'telegram'))}]: "
    ).strip()

    telegram_config = {
        "api_id": int(api_id) if api_id else tg.get("api_id", 0),
        "api_hash": api_hash or tg.get("api_hash", ""),
        "default_account": telegram_account,
        "store_dir": telegram_store_dir or tg.get("store_dir", str(CONFIG_DIR / "telegram")),
    }
    telegram_accounts[telegram_account] = {
        "session_name": session_name
        or telegram_account_config.get("session_name", DEFAULT_SESSION_NAME),
    }

    # Matrix
    matrix_accounts = {
        str(name): values
        for name, values in existing.get("matrix_accounts", {}).items()
        if isinstance(values, dict)
    }
    if DEFAULT_MATRIX_ACCOUNT not in matrix_accounts and isinstance(existing.get("matrix"), dict):
        matrix_accounts[DEFAULT_MATRIX_ACCOUNT] = existing["matrix"]

    matrix_account = input(f"  Account [{DEFAULT_MATRIX_ACCOUNT}]: ").strip()
    matrix_account = matrix_account or DEFAULT_MATRIX_ACCOUNT
    mx = matrix_accounts.get(matrix_account, {})
    print("\n[Matrix] (optional, press Enter to skip)")
    base_url = input(f"  Base URL [{mx.get('base_url', '')}]: ").strip()
    user_id = input(f"  User ID [{mx.get('user_id', '')}]: ").strip()
    device_name = input(f"  Device Name [{mx.get('device_name', 'telefire')}]: ").strip()
    matrix_store_dir = input(
        f"  Store Dir [{mx.get('store_dir', str(CONFIG_DIR / 'matrix'))}]: "
    ).strip()
    password = input(f"  Password [{mx.get('password', '')}]: ").strip()

    matrix_config = None
    if base_url or user_id or mx.get("base_url") or mx.get("user_id"):
        matrix_config = {
            "base_url": base_url or mx.get("base_url", ""),
            "user_id": user_id or mx.get("user_id", ""),
            "device_name": device_name or mx.get("device_name", "telefire"),
            "store_dir": matrix_store_dir or mx.get("store_dir", str(CONFIG_DIR / "matrix" / matrix_account)),
        }
        if password or mx.get("password"):
            matrix_config["password"] = password or mx.get("password", "")

    if matrix_config is not None:
        matrix_accounts[matrix_account] = matrix_config

    # Write TOML
    lines = ["[telegram]"]
    lines.append(f"api_id = {telegram_config['api_id']}")
    lines.append(f'api_hash = "{telegram_config["api_hash"]}"')
    lines.append(f'default_account = "{telegram_config["default_account"]}"')
    lines.append(f'store_dir = "{telegram_config["store_dir"]}"')

    for account_name, account_config in telegram_accounts.items():
        lines.append(f"\n[telegram_accounts.{account_name}]")
        lines.append(f'session_name = "{account_config["session_name"]}"')

    for account_name, account_config in matrix_accounts.items():
        lines.append(f"\n[matrix_accounts.{account_name}]")
        lines.append(f'base_url = "{account_config["base_url"]}"')
        lines.append(f'user_id = "{account_config["user_id"]}"')
        lines.append(f'device_name = "{account_config["device_name"]}"')
        lines.append(f'store_dir = "{account_config["store_dir"]}"')
        if account_config.get("password"):
            lines.append(f'password = "{account_config["password"]}"')

    CONFIG_FILE.write_text("\n".join(lines) + "\n")
    print(f"\nSaved to {CONFIG_FILE}")
