from telefire.matrix import MatrixCommand
from telefire.plugins.base import PluginMount
from mautrix.types import EventType, MessageEvent


class Action(MatrixCommand, metaclass=PluginMount):
    command_name = "matrix_plus_mode"

    def __call__(self):

        def setup(matrix):
            @matrix.client.on(EventType.ROOM_MESSAGE)
            async def _inner(event: MessageEvent):
                if event.sender != matrix.user_id:
                    return
                matrix.logger.info(str(event))

        self.run_matrix_forever(setup=setup)
