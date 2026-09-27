"""Secure file storage for uploaded statements.

* Files are stored under ``STORAGE_DIR`` using a random internal key; the user's
  filename is never used to build a path.
* Every resolved path is checked to be inside the storage root.
* File type is decided from magic bytes, not from the extension or client MIME type.
* Paths are never returned to API clients.
"""

from __future__ import annotations

import hashlib
import io
import os
import re
import secrets
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from app.core.config import get_settings

STORAGE_KEY_RE = re.compile(r"^[a-f0-9]{32}\.(pdf|png|jpg|webp)$")


class UploadValidationError(ValueError):
    """Raised when an upload is rejected. The message is safe to show to users."""


@dataclass(frozen=True)
class DetectedType:
    kind: str  # pdf | image
    mime: str
    ext: str


_SIGNATURES: list[tuple[bytes, DetectedType]] = [
    (b"%PDF-", DetectedType("pdf", "application/pdf", "pdf")),
    (b"\x89PNG\r\n\x1a\n", DetectedType("image", "image/png", "png")),
    (b"\xff\xd8\xff", DetectedType("image", "image/jpeg", "jpg")),
]


def detect_type(head: bytes) -> DetectedType | None:
    for sig, detected in _SIGNATURES:
        if head.startswith(sig):
            return detected
    if len(head) >= 12 and head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return DetectedType("image", "image/webp", "webp")
    # Some PDFs have a few junk bytes before the header; the spec allows up to 1024.
    idx = head.find(b"%PDF-")
    if 0 < idx < 1024:
        return DetectedType("pdf", "application/pdf", "pdf")
    return None


def sanitize_filename(name: str | None) -> str:
    """Produce a display-only filename. Never used for filesystem access."""
    if not name:
        return "statement"
    name = unicodedata.normalize("NFKC", name)
    name = name.replace("\\", "/").split("/")[-1]  # drop any directory components
    name = re.sub(r"[\x00-\x1f\x7f]", "", name)
    name = re.sub(r"[^\w.\- ()\[\]]", "_", name).strip(" .")
    return (name or "statement")[:150]


def storage_root() -> Path:
    root = Path(get_settings().storage_dir).resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def path_for_key(storage_key: str) -> Path:
    """Resolve an internal storage key to a path, refusing anything outside the root."""
    if not STORAGE_KEY_RE.fullmatch(storage_key):
        raise ValueError("invalid storage key")
    root = storage_root()
    path = (root / storage_key).resolve()
    if path.parent != root:
        raise ValueError("storage key escapes storage root")
    return path


@dataclass
class StoredFile:
    storage_key: str
    detected: DetectedType
    size_bytes: int
    sha256: str
    page_count: int | None


def _validate_pdf(data: bytes) -> int:
    import pypdfium2 as pdfium

    try:
        pdf = pdfium.PdfDocument(data)
    except Exception as exc:
        # pdfium raises on password-protected or corrupt files.
        raise UploadValidationError(
            "The PDF could not be opened. It may be corrupted or password-protected."
        ) from exc
    try:
        pages = len(pdf)
    finally:
        pdf.close()
    if pages == 0:
        raise UploadValidationError("The PDF has no pages.")
    limit = get_settings().max_pdf_pages
    if pages > limit:
        raise UploadValidationError(f"The PDF has {pages} pages; the limit is {limit}.")
    return pages


def _validate_image(data: bytes) -> None:
    from PIL import Image

    settings = get_settings()
    try:
        with Image.open(io.BytesIO(data)) as img:
            width, height = img.size
            if width * height > settings.max_image_pixels:
                raise UploadValidationError("The image resolution is too large.")
            img.verify()
    except UploadValidationError:
        raise
    except Exception as exc:
        raise UploadValidationError("The image file is corrupted or not a supported format.") from exc


def store_upload(stream: BinaryIO) -> StoredFile:
    """Validate and persist an upload stream. Reads at most ``max_upload_bytes + 1`` bytes."""
    settings = get_settings()
    limit = settings.max_upload_bytes
    data = stream.read(limit + 1)
    if len(data) > limit:
        raise UploadValidationError(f"File is larger than the {settings.max_upload_mb} MB limit.")
    if len(data) == 0:
        raise UploadValidationError("The file is empty.")
    detected = detect_type(data[:1024])
    if detected is None:
        raise UploadValidationError("Unsupported file type. Upload a PDF, PNG, JPEG or WEBP file.")

    page_count: int | None
    if detected.kind == "pdf":
        page_count = _validate_pdf(data)
    else:
        _validate_image(data)
        page_count = 1

    storage_key = f"{secrets.token_hex(16)}.{detected.ext}"
    path = path_for_key(storage_key)
    # O_EXCL: never overwrite an existing file.
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
    fd = os.open(path, flags, 0o600)
    with os.fdopen(fd, "wb") as fh:
        fh.write(data)
    return StoredFile(
        storage_key=storage_key,
        detected=detected,
        size_bytes=len(data),
        sha256=hashlib.sha256(data).hexdigest(),
        page_count=page_count,
    )


def delete_file(storage_key: str) -> bool:
    try:
        path = path_for_key(storage_key)
    except ValueError:
        return False
    try:
        path.unlink()
        return True
    except FileNotFoundError:
        return False
