import hashlib
from unittest.mock import Mock

import pytest

from app.upload_service import UploadService
from core.errors import ValidationError
from domain.ports import DuplicateContractError, UploadSink
from fakes import InMemoryUploadSink

PDF = b"%PDF-1.7\nhello"


def test_upload_returns_contract_with_correct_fields():
    service = UploadService(InMemoryUploadSink())

    contract = service.upload("contract.pdf", PDF)

    assert contract.filename == "contract.pdf"
    assert contract.file_format == "pdf"
    assert contract.sha256 == hashlib.sha256(PDF).hexdigest()
    assert contract.size_bytes == len(PDF)
    assert contract.status == "uploaded"
    assert contract.error is None
    assert contract.created_at.tzinfo is not None
    assert len(contract.contract_id) == 32


def test_duplicate_upload_raises():
    service = UploadService(InMemoryUploadSink())
    service.upload("contract.pdf", PDF)

    with pytest.raises(DuplicateContractError):
        service.upload("copy.pdf", PDF)


def test_sink_save_rejects_duplicate_sha256_even_without_precheck():
    sink = InMemoryUploadSink()
    service = UploadService(sink)
    contract = service.upload("contract.pdf", PDF)

    with pytest.raises(DuplicateContractError):
        sink.save(contract, PDF)


def test_validation_failure_never_reaches_sink():
    sink = Mock(spec=UploadSink)
    service = UploadService(sink)

    with pytest.raises(ValidationError):
        service.upload("malware.exe", b"content")

    sink.exists_by_sha256.assert_not_called()
    sink.save.assert_not_called()
