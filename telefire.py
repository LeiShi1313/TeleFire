import fire
from plugins.base import Commands
from dotenv import load_dotenv

load_dotenv()

if __name__ == '__main__':
    fire.Fire(Commands)
