from telefire.matrix import MatrixCommand
from telefire.plugins.base import PluginMount


class Action(MatrixCommand, metaclass=PluginMount):
    command_name = "matrix_list_rooms"

    def __call__(self):

        async def _inner(matrix):
            rooms = await matrix.client.get_joined_rooms()
            for room in rooms:
                name = await matrix.get_room_display_name(room)
                matrix.logger.info(f"{room}: {name}")

        self.run_matrix(_inner)
