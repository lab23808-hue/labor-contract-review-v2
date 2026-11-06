import hashlib
from unittest.mock import Mock

import pytest

from app.contract_manager import ContractManager
from core.errors import ContractNotFoundError, ValidationError
from domain.ports import ContractRepository, DuplicateContractError
from fakes import InMemoryContractRepository
from infra.upload_validator import FileUploadValidator

PDF = b"%PDF-1.7\nhello"


def test_upload_returns_contract_and_get_roundtrips():
    manager = ContractManager(InMemoryContractRepository(), {}, FileUploadValidator())

    contract = manager.upload("contract.pdf", PDF)

    assert contract.filename == "contract.pdf"
    assert contract.file_format == "pdf"
    assert contract.sha256 == hashlib.sha256(PDF).hexdigest()
    assert contract.size_bytes == len(PDF)
    assert contract.status == "uploaded"
    assert contract.error is None
    assert contract.created_at.tzinfo is not None
    assert len(contract.contract_id) == 32
    assert manager.get(contract.contract_id) == contract
    with pytest.raises(ContractNotFoundError):
        manager.get("missing")


def test_duplicate_and_validation_failures():
    manager = ContractManager(InMemoryContractRepository(), {}, FileUploadValidator())
    existing = manager.upload("contract.pdf", PDF)

    with pytest.raises(DuplicateContractError) as excinfo:
        manager.upload("copy.pdf", PDF)
    assert existing.contract_id in str(excinfo.value)

    repository = Mock(spec=ContractRepository)
    with pytest.raises(ValidationError):
        ContractManager(repository, {}, FileUploadValidator()).upload("malware.exe", b"content")
    repository.exists_by_sha256.assert_not_called()
    repository.save.assert_not_called()


def test_exists_and_list_delegate_to_repository():
    manager = ContractManager(InMemoryContractRepository(), {}, FileUploadValidator())
    first = manager.upload("a.txt", b"first")
    second = manager.upload("b.txt", b"second")

    assert manager.exists_by_sha256(first.sha256)
    assert not manager.exists_by_sha256("0" * 64)
    assert len(manager.list(limit=1, offset=0)) == 1
    assert {first.contract_id, second.contract_id} == {
        contract.contract_id for contract in manager.list(limit=10, offset=0)
    }
