from telefire.runtime import ServiceCommand
from telefire.telegram.config import TelegramRuntimeConfig
from telefire.telegram.helpers import TelegramInteractionHelper, TelegramLogHelper
from telefire.telegram.service import TelegramService


class TelegramCommand(ServiceCommand):
    def __init__(self, session: str = "test", log_level: str = "info"):
        self.telegram = TelegramService(
            TelegramRuntimeConfig.from_env(session=session),
            log_level=log_level,
        )
        super().__init__(self.telegram, self.telegram.logger)
        self._client = self.telegram.client
        self.log_helper = TelegramLogHelper(self._logger)
        self.interactions = TelegramInteractionHelper(
            self._client,
            self._logger,
            self.log_helper,
        )

    def _set_file_handler(self, method, channel=None, user=None, query=None):
        return self.log_helper.set_file_handler(
            method,
            channel=channel,
            user=user,
            query=query,
        )

    def _log_message(self, msg, channel, user):
        return self.log_helper.log_message(msg, channel, user)

    async def _send_to_ifttt_async(self, event, key, header, body, url):
        return await self.interactions.send_to_ifttt_async(
            event,
            key,
            header,
            body,
            url,
        )

    async def _iter_messages_async(
        self,
        chat,
        user,
        query,
        output,
        print_stat=False,
        cut_func=None,
        offset_date=None,
        min_date=None,
    ):
        return await self.interactions.iter_messages_async(
            chat,
            user,
            query,
            output,
            print_stat=print_stat,
            cut_func=cut_func,
            offset_date=offset_date,
            min_date=min_date,
        )

    async def _get_entity(self, entity_like):
        return await self.interactions.get_entity(entity_like)

    def _is_same_entity(self, entity, other):
        return self.interactions.is_same_entity(entity, other)

    async def _get_sender(self, msg):
        return await self.interactions.get_sender(msg)

    def _parse_msg(self, msg, key, regex):
        return self.interactions.parse_msg(msg, key, regex)

    def _clean_entity(self, msg, key):
        return self.interactions.clean_entity(msg, key)

    async def _parse_entity(self, msg: str, entity_name: str):
        return await self.interactions.parse_entity(msg, entity_name)

    def run_telegram(self, action):
        return self.run_once(action)

    def run_telegram_forever(self, setup=None):
        return self.run_forever(
            setup=setup,
            runner=self.telegram.wait_until_disconnected,
        )
