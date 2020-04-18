from telethon.tl.functions.messages import SearchRequest
from telethon.tl.functions.channels import CreateChannelRequest
from telethon.tl.types import InputMessagesFilterEmpty

from plugins.base import Telegram


class SearchMessages(Telegram):
    name = 'search_messages'

    async def _search_messages_async(self, chat, query, slow, limit, user, output):
        _filter = InputMessagesFilterEmpty()
        peer = await self._client.get_entity(chat)
        if user is not None:
            user = await self._client.get_entity(user)

        self._set_file_handler('search_messages', peer, user, query)

        if slow:
            if output == 'channel':
                result = await self._client(CreateChannelRequest(
                    "Search Messages",
                    "Messages in {}".format(peer.title)))
                output = result.chats[0]
                self._logger.info("Channel: {} created.".format(output.title))
            await self._iter_messages_async(peer, user, query, output)
        else:
            search_request = SearchRequest(
                    peer=peer,
                    q=query,
                    filter=_filter,
                    min_date=None,
                    max_date=None,
                    offset_id=0,
                    add_offset=0,
                    limit=limit,
                    max_id=0,
                    min_id=0,
                    hash=0,
                    from_id=user)
            result = await self._client(search_request)
            for msg in result.messages:
                sender = user
                if sender is None:
                    sender = await self._client.get_entity(msg.from_id)
                self._log_message(msg, peer, sender)

    def action(self, chat, query, slow=False, limit=100, user=None, output='log'):
        with self._client:
            self._client.loop.run_until_complete(
                    self._search_messages_async(chat, query, slow, limit, user, output))
