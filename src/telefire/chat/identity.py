from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol
from urllib.parse import quote


ExternalId = int | str


class IdentityCodec(Protocol):
    source: str

    def actor_id(self, actor_id: ExternalId) -> str: ...

    def scope_id(self, scope_id: ExternalId) -> str: ...

    def message_source_id(
        self,
        scope_id: ExternalId,
        message_id: ExternalId,
    ) -> str: ...

    def thread_document_id(
        self,
        scope_id: ExternalId,
        root_message_id: ExternalId,
    ) -> str: ...

    def revision_document_id(
        self,
        scope_id: ExternalId,
        message_id: ExternalId,
    ) -> str: ...


@dataclass(frozen=True, slots=True)
class NamespacedIdentityCodec:
    source: str
    actor_kind: str
    scope_kind: str

    def __post_init__(self) -> None:
        for value in (self.source, self.actor_kind, self.scope_kind):
            if not value or any(character in value for character in ": \t\r\n"):
                raise ValueError("Identity namespace components must be non-empty tokens")

    def actor_id(self, actor_id: ExternalId) -> str:
        return f"{self.source}:{self.actor_kind}:{_component(actor_id)}"

    def scope_id(self, scope_id: ExternalId) -> str:
        return f"{self.source}:{self.scope_kind}:{_component(scope_id)}"

    def message_source_id(
        self,
        scope_id: ExternalId,
        message_id: ExternalId,
    ) -> str:
        return (
            f"{self.source}:message:{_component(scope_id)}:"
            f"{_component(message_id)}"
        )

    def thread_document_id(
        self,
        scope_id: ExternalId,
        root_message_id: ExternalId,
    ) -> str:
        return (
            f"{self.source}:thread:{_component(scope_id)}:"
            f"{_component(root_message_id)}"
        )

    def revision_document_id(
        self,
        scope_id: ExternalId,
        message_id: ExternalId,
    ) -> str:
        return (
            f"{self.source}:revision:{_component(scope_id)}:"
            f"{_component(message_id)}"
        )


def _component(value: ExternalId) -> str:
    if isinstance(value, bool):
        raise ValueError("Boolean values are not valid external IDs")
    normalized = str(value).strip()
    if not normalized:
        raise ValueError("External IDs cannot be empty")
    return quote(normalized, safe="-_.~")
