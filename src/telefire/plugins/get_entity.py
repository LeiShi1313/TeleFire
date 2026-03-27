from telefire.plugins.base import PluginMount
from telefire.telegram import TelegramCommand


class Action(TelegramCommand, metaclass=PluginMount):
    command_name = "get_entity"

    async def _get_entity_async(self, entity):
        print(await self.helpers.entities.get(entity))

    def __call__(self, entity):
        self.run_once(lambda: self._get_entity_async(entity))
