import fire
from telefire.config import apply_config, init_config
from telefire.plugins import load_plugins
from telefire.plugins.base import Commands


# Register init as a command
def _init():
    """Interactive setup — saves Telegram/Matrix credentials to ~/.telefire/config.toml"""
    init_config()

setattr(Commands, 'init', staticmethod(_init))


def main():
    load_plugins()
    apply_config()
    fire.Fire(Commands)


if __name__ == '__main__':
    main()
