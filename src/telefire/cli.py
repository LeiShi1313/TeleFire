import fire
from telefire.plugins.base import Commands
from dotenv import load_dotenv


def main():
    load_dotenv()
    fire.Fire(Commands)


if __name__ == '__main__':
    main()
