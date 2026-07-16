from __future__ import annotations

from typing import Any, Literal, Protocol


ChatPresentation = Literal["plain", "agent"]


class SentMessage(Protocol):
    id: int | str
    text: str | None


class ChatTransport(Protocol):
    async def get_reply(self, message: Any) -> Any | None: ...

    async def reply(
        self,
        message: Any,
        text: str,
        *,
        presentation: ChatPresentation,
    ) -> SentMessage: ...

    async def update(
        self,
        message: SentMessage,
        text: str,
        *,
        presentation: ChatPresentation,
        wait: bool,
    ) -> bool: ...

    async def delete(self, message: Any) -> None: ...

    def is_outgoing(self, message: Any) -> bool: ...
