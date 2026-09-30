"""F4 — extract plain text from .pdf (text layer), .docx and .txt resumes. No OCR.

Files are processed in memory and never written to disk. Every file is checked by
extension AND content signature, and size/page/zip limits guard against malicious input.
Failures are returned as ParsedFile.error codes so one bad file never breaks a batch.
"""
from __future__ import annotations

import hashlib
import io
import logging
import re
import unicodedata
import zipfile
from dataclasses import dataclass
from pathlib import PurePath

log = logging.getLogger(__name__)

ALLOWED_EXTENSIONS = (".pdf", ".docx", ".txt")
MAX_PDF_PAGES = 40
MAX_DOCX_UNCOMPRESSED = 40 * 1024 * 1024
MAX_DOCX_ENTRIES = 2_000
MIN_TEXT_CHARS = 40

# Error codes (translated in the UI as "parse_error.<code>")
UNSUPPORTED = "unsupported_type"
TOO_LARGE = "too_large"
EMPTY = "empty"
ENCRYPTED = "encrypted"
NO_TEXT_LAYER = "no_text_layer"
CORRUPT = "corrupt"
MISMATCH = "content_mismatch"


@dataclass(frozen=True)
class ParsedFile:
    filename: str
    sha256: str
    text: str = ""
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


class _ParseFailure(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def display_name(filename: str | None) -> str:
    """Basename only, control characters stripped, length capped. Rendered escaped."""
    name = PurePath((filename or "").replace("\\", "/")).name
    name = "".join(ch for ch in name if unicodedata.category(ch)[0] != "C").strip()
    return (name or "resume")[:200]


def normalize_text(text: str, max_chars: int) -> str:
    text = unicodedata.normalize("NFC", text)
    text = "".join(ch for ch in text if ch in "\n\t" or unicodedata.category(ch)[0] != "C")
    text = re.sub(r"[ \t ]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()[:max_chars]


def _pdf_text(data: bytes) -> str:
    if not data[:1024].lstrip().startswith(b"%PDF-"):
        raise _ParseFailure(MISMATCH)
    from pypdf import PdfReader
    from pypdf.errors import PdfReadError

    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            try:
                if not reader.decrypt(""):
                    raise _ParseFailure(ENCRYPTED)
            except _ParseFailure:
                raise
            except Exception as exc:  # noqa: BLE001 - unsupported encryption schemes
                raise _ParseFailure(ENCRYPTED) from exc
        pages = reader.pages[:MAX_PDF_PAGES]
        return "\n".join((page.extract_text() or "") for page in pages)
    except _ParseFailure:
        raise
    except (PdfReadError, ValueError, KeyError, TypeError, AttributeError, RecursionError) as exc:
        raise _ParseFailure(CORRUPT) from exc


def _docx_text(data: bytes) -> str:
    if not data.startswith(b"PK\x03\x04"):
        raise _ParseFailure(MISMATCH)
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            entries = archive.infolist()
            if len(entries) > MAX_DOCX_ENTRIES or sum(e.file_size for e in entries) > MAX_DOCX_UNCOMPRESSED:
                raise _ParseFailure(TOO_LARGE)
            if "word/document.xml" not in archive.namelist():
                raise _ParseFailure(MISMATCH)
    except zipfile.BadZipFile as exc:
        raise _ParseFailure(CORRUPT) from exc

    from docx import Document

    try:
        document = Document(io.BytesIO(data))
    except Exception as exc:  # noqa: BLE001 - python-docx raises many types on bad XML
        raise _ParseFailure(CORRUPT) from exc

    parts = [p.text for p in document.paragraphs]
    for table in document.tables:
        for row in table.rows:
            cells = []
            for cell in row.cells:
                if cell.text not in cells:  # merged cells repeat their text
                    cells.append(cell.text)
            parts.append(" | ".join(cells))
    for section in document.sections:
        parts.extend(p.text for p in section.header.paragraphs)
    return "\n".join(parts)


def _txt_text(data: bytes) -> str:
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return data.decode("utf-16")
    if b"\x00" in data:
        raise _ParseFailure(MISMATCH)
    for encoding in ("utf-8-sig", "cp1251"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise _ParseFailure(CORRUPT)


_EXTRACTORS = {".pdf": _pdf_text, ".docx": _docx_text, ".txt": _txt_text}


def parse_file(filename: str | None, data: bytes, *, max_bytes: int, max_chars: int) -> ParsedFile:
    name = display_name(filename)
    digest = hashlib.sha256(data).hexdigest()
    extension = PurePath(name).suffix.lower()

    def fail(code: str) -> ParsedFile:
        return ParsedFile(name, digest, error=code)

    if extension not in _EXTRACTORS:
        return fail(UNSUPPORTED)
    if len(data) > max_bytes:
        return fail(TOO_LARGE)
    if not data.strip():
        return fail(EMPTY)
    try:
        raw = _EXTRACTORS[extension](data)
    except _ParseFailure as failure:
        return fail(failure.code)
    except Exception:  # noqa: BLE001 - last resort so a single file never breaks the batch
        log.exception("Unexpected parser failure for %s", extension)
        return fail(CORRUPT)

    text = normalize_text(raw, max_chars)
    if len(text) < MIN_TEXT_CHARS:
        return fail(NO_TEXT_LAYER if extension == ".pdf" else EMPTY)
    return ParsedFile(name, digest, text=text)
