from mautrix.errors import MatrixError
from mautrix.types import EventType, RoomID


class MatrixAccountHelper:
    def __init__(self, service, logger):
        self.service = service
        self.logger = logger

    async def current(self) -> dict:
        whoami = await self.service.client.whoami()
        result = {
            "account": self.service.config.account,
            "user_id": str(whoami.user_id),
            "device_id": str(whoami.device_id) if whoami.device_id else None,
            "is_guest": whoami.is_guest,
        }

        try:
            profile = await self.service.client.get_profile(whoami.user_id)
        except MatrixError as exc:
            self.logger.debug(f"Error getting profile for {whoami.user_id}: {exc}")
        else:
            result["displayname"] = getattr(profile, "displayname", None)
            avatar_url = getattr(profile, "avatar_url", None)
            result["avatar_url"] = str(avatar_url) if avatar_url else None

        return result


class MatrixRoomsHelper:
    def __init__(self, service, logger):
        self.service = service
        self.logger = logger
        self._room_name_cache = {}

    async def display_name(self, room_id: RoomID) -> str:
        room_key = str(room_id)
        cached = self._room_name_cache.get(room_key)
        if cached:
            return cached

        try:
            state = await self.service.client.get_state_event(
                room_id=room_id,
                event_type=EventType.ROOM_NAME,
            )
            name = getattr(state, "name", None)
            if name:
                self._room_name_cache[room_key] = name
                return name
        except MatrixError as exc:
            self.logger.debug(f"Error getting room name for {room_id}: {exc}")

        try:
            state = await self.service.client.get_state_event(
                room_id=room_id,
                event_type=EventType.ROOM_CANONICAL_ALIAS,
            )
            alias = getattr(state, "canonical_alias", None)
            if alias:
                alias_name = str(alias)
                self._room_name_cache[room_key] = alias_name
                return alias_name
        except MatrixError as exc:
            self.logger.debug(f"Error getting room canonical alias for {room_id}: {exc}")

        return room_key


class MatrixHelpers:
    def __init__(self, service, logger):
        self.account = MatrixAccountHelper(service, logger)
        self.rooms = MatrixRoomsHelper(service, logger)
