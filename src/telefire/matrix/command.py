from telefire.matrix.config import MatrixRuntimeConfig
from telefire.matrix.helpers import MatrixRoomHelper
from telefire.matrix.service import MatrixService
from telefire.runtime import ServiceCommand


class MatrixCommand(ServiceCommand):
    def __init__(self, log_level: str = "info"):
        self.matrix = MatrixService(MatrixRuntimeConfig.from_env(), log_level=log_level)
        super().__init__(self.matrix, self.matrix.logger)
        self.rooms = MatrixRoomHelper(self.matrix, self._logger)

    def run_matrix(self, action):
        return self.run_once(action)

    def run_matrix_forever(self, setup=None, filter_data=None):
        return self.run_forever(
            setup=setup,
            runner=lambda: self.matrix.start_sync(filter_data=filter_data),
        )
