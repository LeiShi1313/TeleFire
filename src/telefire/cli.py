import fire
from telefire.config import apply_config, init_config
from telefire.plugins import load_plugins
from telefire.plugins.base import command_registry


# Register init as a command
def _init():
    """Interactive setup — saves Telegram/Matrix credentials to ~/.telefire/config.toml"""
    init_config()

command_registry.register_callable(_init, name="init")


def main():
    load_plugins()
    apply_config()
    fire.Fire(command_registry.as_fire_commands())


if __name__ == '__main__':
    main()
