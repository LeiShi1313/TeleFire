from telefire.matrix import MatrixCommand
from telefire.plugins.base import PluginMount


class Action(MatrixCommand, metaclass=PluginMount):
    command_name = "matrix_list_rooms"

    def __call__(self):
        async def _inner():
            rooms = await self.client.get_joined_rooms()
            for room in rooms:
                name = await self.helpers.rooms.display_name(room)
                self.logger.info(f"{room}: {name}")

        self.run_once(_inner)
