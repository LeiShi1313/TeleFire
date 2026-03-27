from telefire.matrix.config import MatrixRuntimeConfig
from telefire.matrix.helpers import MatrixHelpers
from telefire.matrix.service import MatrixService
from telefire.runtime import ServiceCommand


class MatrixCommand(ServiceCommand):
    def __init__(self, log_level: str = "info"):
        service = MatrixService(MatrixRuntimeConfig.from_env(), log_level=log_level)
        super().__init__(service, service.logger)
        self.helpers = MatrixHelpers(self.service, self.logger)

    def run_forever(self, setup=None, filter_data=None):
        return super().run_forever(
            setup=setup,
            runner=lambda: self.service.start_sync(filter_data=filter_data),
        )
