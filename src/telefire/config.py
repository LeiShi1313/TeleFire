"""
Configuration management for telefire.
Loads from ~/.telefire/config.toml with environment variable overrides.
"""
import os
import sys
from pathlib import Path

if sys.version_info >= (3, 11):
    import tomllib
else:
    try:
        import tomllib
    except ModuleNotFoundError:
        import tomli as tomllib

CONFIG_DIR = Path.home() / ".telefire"
CONFIG_FILE = CONFIG_DIR / "config.toml"


def load_config() -> dict:
    """Load config from ~/.telefire/config.toml, with env var overrides."""
    config = {}
    if CONFIG_FILE.exists():
        with open(CONFIG_FILE, "rb") as f:
            config = tomllib.load(f)

    telegram = config.get("telegram", {})
    matrix = config.get("matrix", {})

    return {
        "TELEGRAM_API_ID": os.environ.get("TELEGRAM_API_ID") or str(telegram.get("api_id", "")),
        "TELEGRAM_API_HASH": os.environ.get("TELEGRAM_API_HASH") or telegram.get("api_hash", ""),
        "MATRIX_BASE_URL": os.environ.get("MATRIX_BASE_URL") or matrix.get("base_url", ""),
        "MATRIX_USER_ID": os.environ.get("MATRIX_USER_ID") or matrix.get("user_id", ""),
        "MATRIX_PASSWORD": os.environ.get("MATRIX_PASSWORD") or matrix.get("password", ""),
    }


def apply_config():
    """Load config and set as environment variables (for Telegram/Matrix base classes).
    Priority: env vars > config.toml > .env file
    """
    # Load .env as lowest-priority fallback
    env_file = Path.cwd() / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, value = line.partition("=")
                key, value = key.strip(), value.strip().strip('"').strip("'")
                if key and value and not os.environ.get(key):
                    os.environ[key] = value

    for key, value in load_config().items():
        if value and not os.environ.get(key):
            os.environ[key] = value


def init_config():
    """Interactive first-run setup. Saves credentials to ~/.telefire/config.toml."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)

    existing = {}
    if CONFIG_FILE.exists():
        with open(CONFIG_FILE, "rb") as f:
            existing = tomllib.load(f)

    print("Telefire Setup")
    print("=" * 40)
    print(f"Config file: {CONFIG_FILE}\n")

    # Telegram
    tg = existing.get("telegram", {})
    print("[Telegram] (required)")
    api_id = input(f"  API ID [{tg.get('api_id', '')}]: ").strip()
    api_hash = input(f"  API Hash [{tg.get('api_hash', '')}]: ").strip()

    telegram_config = {
        "api_id": int(api_id) if api_id else tg.get("api_id", 0),
        "api_hash": api_hash or tg.get("api_hash", ""),
    }

    # Matrix
    mx = existing.get("matrix", {})
    print("\n[Matrix] (optional, press Enter to skip)")
    base_url = input(f"  Base URL [{mx.get('base_url', '')}]: ").strip()
    user_id = input(f"  User ID [{mx.get('user_id', '')}]: ").strip()
    password = input(f"  Password [{mx.get('password', '')}]: ").strip()

    matrix_config = {}
    if base_url or mx.get("base_url"):
        matrix_config = {
            "base_url": base_url or mx.get("base_url", ""),
            "user_id": user_id or mx.get("user_id", ""),
            "password": password or mx.get("password", ""),
        }

    # Write TOML
    lines = ["[telegram]"]
    lines.append(f"api_id = {telegram_config['api_id']}")
    lines.append(f'api_hash = "{telegram_config["api_hash"]}"')

    if matrix_config:
        lines.append("\n[matrix]")
        lines.append(f'base_url = "{matrix_config["base_url"]}"')
        lines.append(f'user_id = "{matrix_config["user_id"]}"')
        lines.append(f'password = "{matrix_config["password"]}"')

    CONFIG_FILE.write_text("\n".join(lines) + "\n")
    print(f"\nSaved to {CONFIG_FILE}")
