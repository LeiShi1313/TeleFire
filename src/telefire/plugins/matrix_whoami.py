import json

from telefire.matrix import MatrixCommand
from telefire.plugins.base import PluginMount


class Action(MatrixCommand, metaclass=PluginMount):
    command_name = "matrix_whoami"

    def __call__(self):
        async def _inner():
            print(json.dumps(await self.helpers.account.current(), indent=2, sort_keys=True))

        self.run_once(_inner)
