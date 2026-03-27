"""
Configuration management for telefire.
Loads from ~/.telefire/config.toml with environment variable overrides.
"""
import os
from pathlib import Path

import tomllib
from telefire.constants import DEFAULT_SESSION_NAME

CONFIG_DIR = Path.home() / ".telefire"
CONFIG_FILE = CONFIG_DIR / "config.toml"
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

    # Telegram — default account fields live under [telegram]
    tg = existing.get("telegram", {})

    print("[Telegram] (required)")
    api_id = input(f"  API ID [{tg.get('api_id', '')}]: ").strip()
    api_hash = input(f"  API Hash [{tg.get('api_hash', '')}]: ").strip()
    session_name = input(
        f"  Session Name [{tg.get('session_name', DEFAULT_SESSION_NAME)}]: "
    ).strip()
    telegram_store_dir = input(
        f"  Store Dir [{tg.get('store_dir', str(CONFIG_DIR / 'telegram'))}]: "
    ).strip()

    telegram_config = {
        "api_id": int(api_id) if api_id else tg.get("api_id", 0),
        "api_hash": api_hash or tg.get("api_hash", ""),
        "session_name": session_name or tg.get("session_name", DEFAULT_SESSION_NAME),
        "store_dir": telegram_store_dir or tg.get("store_dir", str(CONFIG_DIR / "telegram")),
    }

    # Preserve existing named Telegram accounts
    telegram_extra_accounts = {
        name: values
        for name, values in tg.items()
        if isinstance(values, dict)
    }

    # Matrix — default account fields live under [matrix]
    mx = existing.get("matrix", {})
    print("\n[Matrix] (optional, press Enter to skip)")
    base_url = input(f"  Base URL [{mx.get('base_url', '')}]: ").strip()
    user_id = input(f"  User ID [{mx.get('user_id', '')}]: ").strip()
    device_name = input(f"  Device Name [{mx.get('device_name', 'telefire')}]: ").strip()
    matrix_store_dir = input(
        f"  Store Dir [{mx.get('store_dir', str(CONFIG_DIR / 'matrix' / 'default'))}]: "
    ).strip()
    password = input(f"  Password [{mx.get('password', '')}]: ").strip()

    matrix_config = None
    if base_url or user_id or mx.get("base_url") or mx.get("user_id"):
        matrix_config = {
            "base_url": base_url or mx.get("base_url", ""),
            "user_id": user_id or mx.get("user_id", ""),
            "device_name": device_name or mx.get("device_name", "telefire"),
            "store_dir": matrix_store_dir or mx.get("store_dir", str(CONFIG_DIR / "matrix" / "default")),
        }
        if password or mx.get("password"):
            matrix_config["password"] = password or mx.get("password", "")

    # Preserve existing named Matrix accounts
    matrix_extra_accounts = {
        name: values
        for name, values in mx.items()
        if isinstance(values, dict)
    }

    # Write TOML
    lines = ["[telegram]"]
    lines.append(f"api_id = {telegram_config['api_id']}")
    lines.append(f'api_hash = "{telegram_config["api_hash"]}"')
    lines.append(f'session_name = "{telegram_config["session_name"]}"')
    lines.append(f'store_dir = "{telegram_config["store_dir"]}"')

    for account_name, account_config in telegram_extra_accounts.items():
        lines.append(f"\n[telegram.{account_name}]")
        lines.append(f'session_name = "{account_config.get("session_name", account_name)}"')

    if matrix_config is not None:
        lines.append("\n[matrix]")
        lines.append(f'base_url = "{matrix_config["base_url"]}"')
        lines.append(f'user_id = "{matrix_config["user_id"]}"')
        lines.append(f'device_name = "{matrix_config["device_name"]}"')
        lines.append(f'store_dir = "{matrix_config["store_dir"]}"')
        if matrix_config.get("password"):
            lines.append(f'password = "{matrix_config["password"]}"')

    for account_name, account_config in matrix_extra_accounts.items():
        lines.append(f"\n[matrix.{account_name}]")
        lines.append(f'base_url = "{account_config.get("base_url", "")}"')
        lines.append(f'user_id = "{account_config.get("user_id", "")}"')
        lines.append(f'device_name = "{account_config.get("device_name", "telefire")}"')
        lines.append(f'store_dir = "{account_config.get("store_dir", "")}"')
        if account_config.get("password"):
            lines.append(f'password = "{account_config["password"]}"')

    CONFIG_FILE.write_text("\n".join(lines) + "\n")
    print(f"\nSaved to {CONFIG_FILE}")
