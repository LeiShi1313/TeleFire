from telefire.runtime import ServiceCommand
from telefire.runtime.file_logging import DailyFileLogger
from telefire.telegram.config import TelegramRuntimeConfig
from telefire.telegram.helpers import TelegramHelpers
from telefire.telegram.service import TelegramService
from telethon import utils


class TelegramCommand(ServiceCommand):
    command_group = "telegram"

    def __init__(
        self,
        account: str = "default",
        session: str | None = None,
        log_level: str = "info",
    ):
        service = TelegramService(
            TelegramRuntimeConfig.from_account(account=account, session=session),
            log_level=log_level,
        )
        super().__init__(service, service.logger)
        self.helpers = TelegramHelpers(self.client, self.logger)
        self.files = DailyFileLogger(self.logger)

    def set_file_handler(self, method, channel=None, user=None, query=None):
        segments = []
        if channel:
            segments.append(channel.title)
        if user:
            segments.append(utils.get_display_name(user))
        return self.files.attach(method, *segments, query=query)

    def run_forever(self, setup=None):
        return super().run_forever(
            setup=setup,
            runner=self.service.wait_until_disconnected,
        )
