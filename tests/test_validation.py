import pytest

from config import settings
from core.errors import ValidationError
from domain.validation import validate_upload

PDF = b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n"
DOCX = b"PK\x03\x04\x14\x00\x06\x00"


def test_pdf_and_docx_magic_consistency():
    assert validate_upload("contract.pdf", PDF) == "pdf"
    assert validate_upload("contract.docx", DOCX) == "docx"
    with pytest.raises(ValidationError):
        validate_upload("contract.pdf", b"not a pdf")
    with pytest.raises(ValidationError):
        validate_upload("contract.docx", b"not a zip")


def test_txt_skips_magic_and_extension_case_is_normalized():
    assert validate_upload("劳动合同.txt", "试用期三个月。".encode("utf-8")) == "txt"
    assert validate_upload("data.txt", bytes(range(256))) == "txt"
    assert validate_upload("CONTRACT.PDF", PDF) == "pdf"
    assert validate_upload("contract.DocX", DOCX) == "docx"


def test_size_boundaries():
    with pytest.raises(ValidationError):
        validate_upload("empty.txt", b"")
    with pytest.raises(ValidationError):
        validate_upload("big.txt", b"a" * (settings.MAX_UPLOAD_BYTES + 1))
    assert validate_upload("limit.txt", b"a" * settings.MAX_UPLOAD_BYTES) == "txt"


def test_illegal_filenames_rejected():
    for filename in ("../etc/passwd", "foo\\bar.txt", "   "):
        with pytest.raises(ValidationError):
            validate_upload(filename, b"x")
