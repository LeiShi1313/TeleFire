from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any


KNOWLEDGE_DIRECTORY_BANK_ID = "system:knowledge-directory"
KNOWLEDGE_DIRECTORY_BANK_NAME = "Knowledge Directory"
KNOWLEDGE_DIRECTORY_SCHEMA = "telefire.knowledge-directory.v1"
_BANK_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9:_.%-]{0,255}$")
_TOKEN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")


@dataclass(frozen=True, slots=True)
class DirectorySource:
    bank_id: str
    display_name: str
    platform: str
    source_kind: str
    attributes: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not is_canonical_bank_id(self.bank_id):
            raise ValueError("Directory source requires a canonical bank ID")
        if not self.display_name.strip() or len(self.display_name) > 256:
            raise ValueError("Directory source display name is invalid")
        if not _TOKEN_RE.fullmatch(self.platform):
            raise ValueError("Directory source platform is invalid")
        if not _TOKEN_RE.fullmatch(self.source_kind):
            raise ValueError("Directory source kind is invalid")
        if len(self.attributes) > 16:
            raise ValueError("Directory source has too many attributes")
        normalized: dict[str, str] = {}
        for key, value in self.attributes.items():
            if not _TOKEN_RE.fullmatch(key) or not isinstance(value, str):
                raise ValueError("Directory source attribute is invalid")
            clean = " ".join(value.split())
            if not clean or len(clean) > 500:
                raise ValueError("Directory source attribute is invalid")
            normalized[key] = clean
        object.__setattr__(self, "display_name", " ".join(self.display_name.split()))
        object.__setattr__(self, "attributes", normalized)


@dataclass(frozen=True, slots=True)
class DirectoryPublication:
    publication_id: str
    publisher_id: str
    published_at: datetime
    source: DirectorySource
    description: str = ""

    def __post_init__(self) -> None:
        if not self.publication_id.strip() or len(self.publication_id) > 256:
            raise ValueError("Directory publication ID is invalid")
        if not is_canonical_actor_id(self.publisher_id):
            raise ValueError("Directory publisher identity is invalid")
        if self.published_at.tzinfo is None:
            raise ValueError("Directory publication time must be timezone-aware")
        description = self.description.strip()
        if len(description) > 4_000:
            raise ValueError("Directory publication description is too long")
        object.__setattr__(self, "publication_id", self.publication_id.strip())
        object.__setattr__(self, "published_at", self.published_at.astimezone(UTC))
        object.__setattr__(self, "description", description)

    @property
    def document_id(self) -> str:
        digest = hashlib.sha256(self.publication_id.encode("utf-8")).hexdigest()
        return f"directory-publication:{digest}"

    @property
    def content(self) -> str:
        lines = [
            f"Knowledge source: {self.source.display_name}",
            f"Platform: {self.source.platform}",
            f"Source type: {self.source.source_kind}",
        ]
        for key, value in sorted(self.source.attributes.items()):
            lines.append(f"{key.replace('_', ' ').title()}: {value}")
        if self.description:
            lines.append(f"Owner description: {self.description}")
        return "\n".join(lines)


@dataclass(frozen=True, slots=True)
class DirectoryEvidence:
    memory_id: str
    text: str
    memory_type: str | None = None
    document_id: str | None = None


@dataclass(frozen=True, slots=True)
class DirectoryReference:
    bank_id: str
    display_name: str
    platform: str
    source_kind: str
    evidence: tuple[DirectoryEvidence, ...]


@dataclass(frozen=True, slots=True)
class DirectoryRecall:
    references: tuple[DirectoryReference, ...]


def is_canonical_bank_id(value: str) -> bool:
    return isinstance(value, str) and _BANK_ID_RE.fullmatch(value) is not None


def is_canonical_actor_id(value: str) -> bool:
    return is_canonical_bank_id(value) and ":user:" in value


def bank_reference_tag(bank_id: str) -> str:
    if not is_canonical_bank_id(bank_id):
        raise ValueError("Invalid canonical bank ID")
    digest = hashlib.sha256(bank_id.encode("utf-8")).hexdigest()
    return f"telefire:bank-ref:{digest}"


def directory_metadata(source: DirectorySource) -> dict[str, str]:
    tag = bank_reference_tag(source.bank_id)
    return {
        "client": "telefire",
        "source": "knowledge-directory",
        "schema": KNOWLEDGE_DIRECTORY_SCHEMA,
        "bank_id": source.bank_id,
        "bank_ref": tag,
        "source_name": source.display_name,
        "source_platform": source.platform,
        "source_kind": source.source_kind,
    }


def parse_reference_metadata(
    *,
    metadata: Any,
    tags: Any,
    allowed_bank_ids: set[str] | None = None,
) -> tuple[str, str, str, str]:
    if not isinstance(metadata, dict) or not isinstance(tags, list):
        raise ValueError("Malformed directory reference")
    required = {
        "client",
        "source",
        "schema",
        "bank_id",
        "bank_ref",
        "source_name",
        "source_platform",
        "source_kind",
    }
    if not required <= metadata.keys() or not all(
        isinstance(metadata[key], str) for key in required
    ):
        raise ValueError("Malformed directory reference")
    bank_id = metadata["bank_id"]
    tag = bank_reference_tag(bank_id)
    if (
        metadata["client"] != "telefire"
        or metadata["source"] != "knowledge-directory"
        or metadata["schema"] != KNOWLEDGE_DIRECTORY_SCHEMA
        or metadata["bank_ref"] != tag
        or tags != [tag]
        or (allowed_bank_ids is not None and bank_id not in allowed_bank_ids)
        or not metadata["source_name"].strip()
        or len(metadata["source_name"]) > 256
        or not _TOKEN_RE.fullmatch(metadata["source_platform"])
        or not _TOKEN_RE.fullmatch(metadata["source_kind"])
    ):
        raise ValueError("Malformed directory reference")
    return (
        bank_id,
        metadata["source_name"],
        metadata["source_platform"],
        metadata["source_kind"],
    )
