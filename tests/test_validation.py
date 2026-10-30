import pytest

from config import settings
from core.errors import ValidationError
from domain.validation import validate_upload

PDF = b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n"
DOCX = b"PK\x03\x04\x14\x00\x06\x00"


def test_pdf_with_valid_magic_passes():
    assert validate_upload("contract.pdf", PDF) == "pdf"


def test_pdf_with_invalid_magic_rejected():
    with pytest.raises(ValidationError):
        validate_upload("contract.pdf", b"this is not a pdf")


def test_docx_with_valid_magic_passes():
    assert validate_upload("contract.docx", DOCX) == "docx"


def test_docx_with_invalid_magic_rejected():
    with pytest.raises(ValidationError):
        validate_upload("contract.docx", b"plain text pretending to be docx")


def test_txt_with_chinese_content_passes():
    content = "劳动合同：试用期三个月。".encode("utf-8")
    assert validate_upload("劳动合同.txt", content) == "txt"


def test_txt_with_arbitrary_bytes_passes():
    assert validate_upload("data.txt", bytes(range(256))) == "txt"


def test_zero_byte_file_rejected():
    with pytest.raises(ValidationError):
        validate_upload("empty.txt", b"")


def test_file_over_size_limit_rejected():
    content = b"a" * (settings.MAX_UPLOAD_BYTES + 1)
    with pytest.raises(ValidationError):
        validate_upload("big.txt", content)


def test_file_exactly_at_size_limit_passes():
    content = b"a" * settings.MAX_UPLOAD_BYTES
    assert validate_upload("limit.txt", content) == "txt"


def test_path_traversal_filename_rejected():
    with pytest.raises(ValidationError):
        validate_upload("../etc/passwd", b"x")


def test_windows_separator_filename_rejected():
    with pytest.raises(ValidationError):
        validate_upload("foo\\bar.txt", b"x")


def test_blank_filename_rejected():
    with pytest.raises(ValidationError):
        validate_upload("   ", b"x")


def test_uppercase_pdf_extension_normalized():
    assert validate_upload("CONTRACT.PDF", PDF) == "pdf"


def test_mixed_case_docx_extension_normalized():
    assert validate_upload("contract.DocX", DOCX) == "docx"
