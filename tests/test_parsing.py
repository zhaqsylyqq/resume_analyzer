import io
import zipfile

import pytest
from pypdf import PdfWriter

from app.services import parsing
from app.services.parsing import display_name, parse_file

from .fakes import make_docx, make_pdf

LIMITS = {"max_bytes": 5 * 1024 * 1024, "max_chars": 30_000}
LONG = "Python developer with Django, PostgreSQL and Docker experience in fintech."


def parse(name, data, **overrides):
    return parse_file(name, data, **{**LIMITS, **overrides})


def test_txt_utf8():
    result = parse("cv.txt", f"Иван Петров\n{LONG}".encode("utf-8"))
    assert result.ok and result.text.startswith("Иван Петров")


def test_txt_cp1251_fallback():
    text = f"Бухгалтер, 1С, налоговая отчётность. {LONG}"
    assert parse("cv.txt", text.encode("cp1251")).text == text


def test_txt_utf16_with_bom():
    text = f"Қазақ тілі — ана тілі. {LONG}"
    assert parse("cv.txt", text.encode("utf-16")).text == text


def test_docx_paragraphs_and_tables():
    data = make_docx(["Айгерим Сапарова", LONG], table=[["Навыки", "Python, SQL"], ["Языки", "KZ, RU, EN"]])
    result = parse("cv.docx", data)
    assert result.ok
    assert "Айгерим Сапарова" in result.text
    assert "Навыки | Python, SQL" in result.text


def test_pdf_text_layer():
    result = parse("cv.pdf", make_pdf(["John Smith", LONG]))
    assert result.ok
    assert "John Smith" in result.text and "PostgreSQL" in result.text


def test_pdf_without_text_is_reported_as_scan():
    assert parse("scan.pdf", make_pdf([])).error == parsing.NO_TEXT_LAYER


def test_encrypted_pdf():
    writer = PdfWriter()
    writer.add_blank_page(200, 200)
    writer.encrypt("secret", algorithm="RC4-128")
    buffer = io.BytesIO()
    writer.write(buffer)
    assert parse("locked.pdf", buffer.getvalue()).error == parsing.ENCRYPTED


@pytest.mark.parametrize(
    "name,data,error",
    [
        ("cv.exe", b"MZ....", parsing.UNSUPPORTED),
        ("cv.doc", b"\xd0\xcf\x11\xe0legacy word", parsing.UNSUPPORTED),
        ("photo.jpg", b"\xff\xd8\xff", parsing.UNSUPPORTED),
        ("cv.pdf", b"not a pdf at all", parsing.MISMATCH),
        ("cv.docx", b"%PDF-1.4 renamed", parsing.MISMATCH),
        ("cv.txt", b"\x00\x01binary\x00", parsing.MISMATCH),
        ("cv.pdf", b"%PDF-1.4\n garbage", parsing.CORRUPT),
        ("cv.docx", b"PK\x03\x04 truncated zip", parsing.CORRUPT),
        ("cv.txt", b"   \n  ", parsing.EMPTY),
        ("cv.txt", b"short", parsing.EMPTY),
    ],
)
def test_bad_files_return_error_codes(name, data, error):
    result = parse(name, data)
    assert not result.ok and result.error == error and result.text == ""


def test_zip_without_word_document_is_mismatch():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("hello.txt", "hi")
    assert parse("cv.docx", buffer.getvalue()).error == parsing.MISMATCH


def test_zip_bomb_is_rejected(monkeypatch):
    monkeypatch.setattr(parsing, "MAX_DOCX_UNCOMPRESSED", 1000)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("word/document.xml", "0" * 50_000)
    assert parse("cv.docx", buffer.getvalue()).error == parsing.TOO_LARGE


def test_size_limit():
    assert parse("cv.txt", LONG.encode() * 10, max_bytes=100).error == parsing.TOO_LARGE


def test_text_is_normalized_and_truncated():
    result = parse("cv.txt", ("A  \t b\x07\n\n\n\n" + LONG).encode(), max_chars=50)
    assert result.text.startswith("A b\n\nPython")
    assert len(result.text) == 50


def test_same_bytes_same_hash():
    data = LONG.encode()
    assert parse("a.txt", data).sha256 == parse("b.txt", data).sha256


@pytest.mark.parametrize(
    "raw,expected",
    [("../../etc/passwd.txt", "passwd.txt"), ("C:\\Users\\x\\Резюме.pdf", "Резюме.pdf"),
     ("bad\x00name.txt", "badname.txt"), ("", "resume"), (None, "resume")],
)
def test_display_name(raw, expected):
    assert display_name(raw) == expected
