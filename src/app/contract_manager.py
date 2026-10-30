import hashlib
import uuid
from datetime import datetime, timezone

from core.contract import Contract
from core.errors import ContractNotFoundError
from domain.ports import ContractRepository, DuplicateContractError
from domain.validation import validate_upload


class ContractManager:
    def __init__(self, repository: ContractRepository) -> None:
        self._repository = repository

    def upload(self, filename: str, content: bytes) -> Contract:
        file_format = validate_upload(filename, content)

        sha256 = hashlib.sha256(content).hexdigest()
        if self._repository.exists_by_sha256(sha256):
            raise DuplicateContractError(f"文件已存在：sha256={sha256}")

        contract = Contract(
            contract_id=uuid.uuid4().hex,
            filename=filename,
            file_format=file_format,
            sha256=sha256,
            size_bytes=len(content),
            created_at=datetime.now(timezone.utc),
        )
        self.save(contract, content)
        return contract

    def save(self, contract: Contract, content: bytes) -> None:
        self._repository.save(contract, content)

    def get(self, contract_id: str) -> Contract:
        contract = self._repository.get(contract_id)
        if contract is None:
            raise ContractNotFoundError(f"合同不存在：{contract_id}")
        return contract

    def list(self, limit: int = 20, offset: int = 0) -> list[Contract]:
        return self._repository.list(limit=limit, offset=offset)

    def exists_by_sha256(self, sha256: str) -> bool:
        return self._repository.exists_by_sha256(sha256)
