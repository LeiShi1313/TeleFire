from telefire.matrix.config import MatrixRuntimeConfig
from telefire.matrix.helpers import MatrixHelpers
from telefire.matrix.service import MatrixService
from telefire.runtime import ServiceCommand


class MatrixCommand(ServiceCommand):
    command_group = "matrix"

    def __init__(
        self,
        account: str = "default",
        log_level: str = "info",
        enable_crypto: bool = False,
    ):
        service = MatrixService(
            MatrixRuntimeConfig.from_account(account=account),
            log_level=log_level,
            enable_crypto=enable_crypto,
        )
        super().__init__(service, service.logger)
        self.helpers = MatrixHelpers(self.service, self.logger)

    def run_forever(self, setup=None, filter_data=None):
        return super().run_forever(
            setup=setup,
            runner=lambda: self.service.start_sync(filter_data=filter_data),
        )
