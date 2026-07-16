from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Protocol


AttachmentKind = Literal[
    "image",
    "audio",
    "video",
    "text",
    "file",
    "sticker",
    "other",
]


@dataclass(frozen=True, slots=True)
class AttachmentReference:
    """Metadata and an opaque adapter key, never attachment payload bytes."""

    key: str
    kind: AttachmentKind
    mime_type: str
    filename: str | None = None
    size_bytes: int | None = None

    def __post_init__(self) -> None:
        if not self.key.strip():
            raise ValueError("Attachment reference key cannot be empty")
        if not self.mime_type.strip():
            raise ValueError("Attachment MIME type cannot be empty")
        if self.size_bytes is not None and self.size_bytes < 0:
            raise ValueError("Attachment size cannot be negative")


@dataclass(frozen=True, slots=True)
class AttachmentDescription:
    context_text: str
    memory_text: str


class AttachmentDescriber(Protocol):
    def has_attachment(self, message: Any) -> bool: ...

    async def describe(self, message: Any) -> AttachmentDescription | None: ...
