import hashlib
import logging
import uuid
from datetime import datetime, timezone

import pytest

from core.contract import Contract
from domain.ports import DuplicateContractError
from infra.sqlite_repository import SQLiteContractRepository

PDF = b"%PDF-1.7\nhello"


def _contract(
    content: bytes, filename: str = "contract.pdf", created_at: datetime | None = None
) -> Contract:
    return Contract(
        contract_id=uuid.uuid4().hex,
        filename=filename,
        file_format=filename.rsplit(".", 1)[1].lower(),
        sha256=hashlib.sha256(content).hexdigest(),
        size_bytes=len(content),
        created_at=created_at or datetime.now(timezone.utc),
    )


def test_save_get_roundtrip_uses_full_sha_directory(tmp_path):
    repository = SQLiteContractRepository(tmp_path)
    contract = _contract(PDF)

    repository.save(contract, PDF)

    expected = tmp_path / "files" / contract.sha256 / "contract.pdf"
    assert expected.read_bytes() == PDF
    assert repository.get(contract.contract_id) == contract


def test_duplicate_and_exists_by_sha256(tmp_path):
    repository = SQLiteContractRepository(tmp_path)
    contract = _contract(PDF)

    assert not repository.exists_by_sha256(contract.sha256)
    repository.save(contract, PDF)
    assert repository.exists_by_sha256(contract.sha256)
    with pytest.raises(DuplicateContractError) as excinfo:
        repository.save(_contract(PDF), PDF)
    assert contract.contract_id in str(excinfo.value)


def test_list_orders_newest_first_and_paginates(tmp_path):
    repository = SQLiteContractRepository(tmp_path)
    payloads = [b"first", b"second", b"third"]
    contracts = [
        _contract(
            payload,
            f"contract-{index}.txt",
            datetime(2026, 1, index + 1, tzinfo=timezone.utc),
        )
        for index, payload in enumerate(payloads)
    ]
    for contract, payload in zip(contracts, payloads):
        repository.save(contract, payload)

    page1 = repository.list(limit=2, offset=0)
    page2 = repository.list(limit=2, offset=2)

    assert [contract.contract_id for contract in page1] == [
        contracts[2].contract_id,
        contracts[1].contract_id,
    ]
    assert [contract.contract_id for contract in page2] == [contracts[0].contract_id]


def test_missing_file_warns_and_restart_persists(tmp_path, caplog):
    contract = _contract(PDF)
    SQLiteContractRepository(tmp_path).save(contract, PDF)

    reopened = SQLiteContractRepository(tmp_path)
    assert reopened.get(contract.contract_id) == contract
    assert reopened.exists_by_sha256(contract.sha256)
    assert [item.contract_id for item in reopened.list(limit=10, offset=0)] == [
        contract.contract_id
    ]

    (tmp_path / "files" / contract.sha256 / "contract.pdf").unlink()
    with caplog.at_level(logging.WARNING):
        assert reopened.get(contract.contract_id) is None
    assert "落盘文件缺失" in caplog.text
