import argparse
import os

from aiohttp import web

from telefire_memory import MemoryCore, MemorySettings
from telefire_memory.http import create_app


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run the standalone Telefire memory service"
    )
    parser.add_argument(
        "--host",
        default=os.environ.get("TELEFIRE_MEMORY_HOST", "127.0.0.1"),
    )
    parser.add_argument(
        "--port",
        default=int(os.environ.get("TELEFIRE_MEMORY_PORT", "8765")),
        type=int,
    )
    args = parser.parse_args()
    web.run_app(
        create_app(MemoryCore(MemorySettings.from_env())),
        host=args.host,
        port=args.port,
    )


if __name__ == "__main__":
    main()
