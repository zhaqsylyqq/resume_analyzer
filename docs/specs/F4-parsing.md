# Spec F4 — Multi-format resume parsing

## Goal
Extract plain text from `.pdf` (text layer), `.docx` and `.txt` resumes, safely and
without letting one bad file break a batch. No OCR.

## Interface (`app/services/parsing.py`)
`parse_file(filename, data, *, max_bytes, max_chars) -> ParsedFile(filename, sha256, text, error)`

## Behaviour
- Display name: basename only, control characters removed, ≤ 200 chars (always rendered
  escaped). Files are processed in memory and never written to disk.
- Allowed extensions: `.pdf`, `.docx`, `.txt`. The content signature must match the
  extension (`%PDF-`, ZIP with `word/document.xml`, no NUL bytes in text).
- PDF: pypdf, first 40 pages; empty-password encryption is opened, anything else →
  `encrypted`; fewer than 40 characters of text → `no_text_layer` (scanned document).
- DOCX: python-docx paragraphs, tables (merged cells de-duplicated) and headers.
  Zip-bomb guard: ≤ 2000 entries and ≤ 40 MB uncompressed.
- TXT: UTF-16 by BOM, then UTF-8 (with/without BOM), then CP1251.
- Text is NFC-normalized, control characters stripped, whitespace collapsed and capped at
  `MAX_RESUME_CHARS` (30 000) before it is stored or sent to Gemini.
- SHA-256 of the bytes identifies duplicates within a session.

## Error codes
`unsupported_type`, `too_large`, `empty`, `encrypted`, `no_text_layer`, `corrupt`,
`content_mismatch` (+ `duplicate` at upload level). Each has RU/KK messages.

## Acceptance
Unit tests cover every format, every error code, zip bombs, encodings, normalization and
name sanitizing. All 20 synthetic samples parse; the 3 bad samples are rejected.
