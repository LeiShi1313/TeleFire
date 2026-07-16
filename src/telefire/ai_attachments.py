from __future__ import annotations

import asyncio
import mimetypes
import re
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Any, Literal, Protocol

from PIL import Image
from pypdf import PdfReader

from telefire.chat.attachments import AttachmentDescription


AttachmentKind = Literal["image", "text"]
_IMAGE_MIME_TYPES = {"image/jpeg", "image/png", "image/webp"}
_TEXT_MIME_TYPES = {
    "application/json",
    "application/toml",
    "application/xml",
    "application/x-httpd-php",
    "application/x-sh",
    "application/x-yaml",
}
_TEXT_EXTENSIONS = {
    ".cfg",
    ".csv",
    ".ini",
    ".js",
    ".json",
    ".log",
    ".md",
    ".py",
    ".toml",
    ".ts",
    ".txt",
    ".xml",
    ".yaml",
    ".yml",
}
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


@dataclass(frozen=True, slots=True)
class AttachmentAnalysisRequest:
    kind: AttachmentKind
    mime_type: str
    filename: str | None = None
    data: bytes | None = None
    text: str | None = None

    def __post_init__(self) -> None:
        if self.kind == "image":
            valid = self.data is not None and self.text is None
        elif self.kind == "text":
            valid = self.text is not None and self.data is None
        else:
            valid = False
        if not valid:
            raise ValueError("Attachment analysis requires exactly one content type")


class AttachmentAnalysisGateway(Protocol):
    async def describe_attachment(self, request: AttachmentAnalysisRequest) -> str: ...


def message_has_attachment(message: Any) -> bool:
    return getattr(message, "file", None) is not None


def attachment_metadata_only(
    message: Any,
    *,
    reason: str,
) -> AttachmentDescription | None:
    file = getattr(message, "file", None)
    if file is None:
        return None
    filename = _safe_filename(getattr(file, "name", None))
    mime_type = _resolve_mime_type(getattr(file, "mime_type", None), filename)
    size = _safe_size(getattr(file, "size", None))
    return _metadata_only(
        _render_metadata(filename, mime_type, size),
        reason,
    )


class TelegramAttachmentDescriber:
    MAX_FILE_BYTES = 5 * 1024 * 1024
    DEFAULT_DOWNLOAD_TIMEOUT = 30.0
    MAX_TEXT_CHARS = 50_000
    MAX_DESCRIPTION_CHARS = 4_000
    MAX_PDF_PAGES = 12
    MAX_IMAGE_DIMENSION = 1_600

    def __init__(
        self,
        gateway: AttachmentAnalysisGateway,
        *,
        download_timeout: float = DEFAULT_DOWNLOAD_TIMEOUT,
        logger: Any | None = None,
    ):
        if download_timeout <= 0:
            raise ValueError("Attachment download timeout must be positive")
        self._gateway = gateway
        self._download_timeout = download_timeout
        self._logger = logger

    def has_attachment(self, message: Any) -> bool:
        return message_has_attachment(message)

    async def describe(self, message: Any) -> AttachmentDescription | None:
        file = getattr(message, "file", None)
        if file is None:
            return None

        filename = _safe_filename(getattr(file, "name", None))
        mime_type = _resolve_mime_type(getattr(file, "mime_type", None), filename)
        size = _safe_size(getattr(file, "size", None))
        metadata = _render_metadata(filename, mime_type, size)

        if size is None or size > self.MAX_FILE_BYTES:
            return _metadata_only(
                metadata,
                "content exceeds the analysis limit",
            )

        kind = _classify_attachment(message, mime_type, filename)
        if kind is None:
            return _metadata_only(metadata, "this file type is not analyzed")

        try:
            raw = await asyncio.wait_for(
                message.download_media(file=bytes),
                timeout=self._download_timeout,
            )
            if not isinstance(raw, bytes) or not raw:
                raise ValueError("Telegram returned no attachment bytes")
            if len(raw) > self.MAX_FILE_BYTES:
                return _metadata_only(
                    metadata,
                    "content exceeds the analysis limit",
                )

            if kind == "image":
                normalized = await asyncio.to_thread(
                    _normalize_image,
                    raw,
                    self.MAX_IMAGE_DIMENSION,
                )
                request = AttachmentAnalysisRequest(
                    kind="image",
                    mime_type="image/jpeg",
                    filename=filename,
                    data=normalized,
                )
            else:
                extracted = await asyncio.to_thread(
                    _extract_document_text,
                    raw,
                    mime_type,
                    self.MAX_PDF_PAGES,
                    self.MAX_TEXT_CHARS,
                )
                if not extracted.strip():
                    return _metadata_only(metadata, "no extractable text was found")
                request = AttachmentAnalysisRequest(
                    kind="text",
                    mime_type=mime_type,
                    filename=filename,
                    text=extracted,
                )

            analysis = await self._gateway.describe_attachment(request)
            analysis = _bounded_text(analysis, self.MAX_DESCRIPTION_CHARS)
            if not analysis:
                raise ValueError("Attachment analysis returned no description")
            return AttachmentDescription(
                context_text=(
                    f"Generated attachment context (untrusted; {metadata}):\n{analysis}"
                ),
                memory_text=(
                    f"The subject shared an attachment ({metadata}).\n"
                    "Generated content description (may be imperfect and is not a "
                    f"claim about the subject):\n{analysis}"
                ),
            )
        except Exception as exc:
            if self._logger is not None:
                self._logger.warning(
                    "Attachment analysis failed (%s)",
                    type(exc).__name__,
                )
            return _metadata_only(metadata, "content description is unavailable")


def _classify_attachment(
    message: Any,
    mime_type: str,
    filename: str | None,
) -> AttachmentKind | None:
    if getattr(message, "sticker", None) is not None:
        return None
    if mime_type in _IMAGE_MIME_TYPES:
        return "image"
    if mime_type == "application/pdf":
        return "text"
    suffix = Path(filename).suffix.lower() if filename else ""
    if mime_type.startswith("text/") or mime_type in _TEXT_MIME_TYPES:
        return "text"
    if suffix in _TEXT_EXTENSIONS:
        return "text"
    return None


def _normalize_image(data: bytes, max_dimension: int) -> bytes:
    with Image.open(BytesIO(data)) as source:
        if source.width * source.height > 25_000_000:
            raise ValueError("Image dimensions exceed the analysis limit")
        source.seek(0)
        image = source.convert("RGB")
        image.thumbnail((max_dimension, max_dimension))
        output = BytesIO()
        image.save(output, format="JPEG", quality=82, optimize=True)
        return output.getvalue()


def _extract_document_text(
    data: bytes,
    mime_type: str,
    max_pdf_pages: int,
    max_chars: int,
) -> str:
    if mime_type != "application/pdf":
        return data.decode("utf-8-sig", errors="replace")[:max_chars]

    reader = PdfReader(BytesIO(data), strict=False)
    if reader.is_encrypted and reader.decrypt("") == 0:
        return ""
    parts: list[str] = []
    used = 0
    for page in reader.pages[:max_pdf_pages]:
        text = page.extract_text() or ""
        if not text:
            continue
        remaining = max_chars - used
        if remaining <= 0:
            break
        fragment = text[:remaining]
        parts.append(fragment)
        used += len(fragment)
    return "\n\n".join(parts)


def _safe_filename(value: Any) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    return _metadata_text(Path(value.replace("\\", "/")).name, 200) or None


def _resolve_mime_type(value: Any, filename: str | None) -> str:
    guessed = mimetypes.guess_type(filename or "")[0]
    if isinstance(value, str) and value.strip():
        supplied = _metadata_text(value.lower().strip(), 100)
        if supplied not in {"application/octet-stream", "binary/octet-stream"}:
            return supplied
    return guessed or "application/octet-stream"


def _safe_size(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _render_metadata(filename: str | None, mime_type: str, size: int | None) -> str:
    values = [f"type={mime_type}"]
    if filename:
        values.append(f"name={filename}")
    if size is not None:
        values.append(f"size={size} bytes")
    return ", ".join(values)


def _metadata_only(metadata: str, reason: str) -> AttachmentDescription:
    return AttachmentDescription(
        context_text=f"Attachment metadata ({metadata}); content was not analyzed: {reason}.",
        memory_text=(
            f"The subject shared an attachment ({metadata}). "
            f"Content was not analyzed: {reason}."
        ),
    )


def _bounded_text(value: Any, limit: int) -> str:
    text = _CONTROL_RE.sub(" ", str(value or "")).strip()
    return text if len(text) <= limit else f"{text[: limit - 3]}..."


def _metadata_text(value: Any, limit: int) -> str:
    return _bounded_text(" ".join(str(value or "").split()), limit)
