import hashlib
from unittest.mock import Mock

import pytest

from app.contract_manager import ContractManager
from core.errors import ContractNotFoundError, ValidationError
from domain.ports import ContractRepository, DuplicateContractError
from fakes import InMemoryContractRepository

PDF = b"%PDF-1.7\nhello"


def test_upload_returns_contract_with_correct_fields():
    manager = ContractManager(InMemoryContractRepository())

    contract = manager.upload("contract.pdf", PDF)

    assert contract.filename == "contract.pdf"
    assert contract.file_format == "pdf"
    assert contract.sha256 == hashlib.sha256(PDF).hexdigest()
    assert contract.size_bytes == len(PDF)
    assert contract.status == "uploaded"
    assert contract.error is None
    assert contract.created_at.tzinfo is not None
    assert len(contract.contract_id) == 32


def test_duplicate_upload_raises():
    manager = ContractManager(InMemoryContractRepository())
    manager.upload("contract.pdf", PDF)

    with pytest.raises(DuplicateContractError):
        manager.upload("copy.pdf", PDF)


def test_validation_failure_never_reaches_repository():
    repository = Mock(spec=ContractRepository)
    manager = ContractManager(repository)

    with pytest.raises(ValidationError):
        manager.upload("malware.exe", b"content")

    repository.exists_by_sha256.assert_not_called()
    repository.save.assert_not_called()


def test_get_missing_contract_raises_not_found():
    manager = ContractManager(InMemoryContractRepository())

    with pytest.raises(ContractNotFoundError):
        manager.get("missing")


def test_exists_and_list_delegate_to_repository():
    manager = ContractManager(InMemoryContractRepository())
    first = manager.upload("a.txt", b"first")
    second = manager.upload("b.txt", b"second")

    assert manager.exists_by_sha256(first.sha256)
    assert not manager.exists_by_sha256("0" * 64)
    assert len(manager.list(limit=1, offset=0)) == 1
    assert {first.contract_id, second.contract_id} == {
        contract.contract_id for contract in manager.list(limit=10, offset=0)
    }
