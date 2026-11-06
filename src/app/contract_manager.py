import hashlib
import uuid
from datetime import datetime, timezone
from typing import Mapping, Optional

from core.contract import Contract
from core.errors import ContractNotFoundError, InvalidStateError
from domain.ports import (
    ContractRepository,
    DocumentParser,
    DocumentValidator,
    DuplicateContractError,
)
from app.upload_policy import (
    DEFAULT_UPLOAD_POLICY,
    UploadPolicy,
    validate_upload,
)


class ContractManager:
    def __init__(
        self,
        repository: ContractRepository,
        parsers: Mapping[str, DocumentParser],
        validator: Optional[DocumentValidator] = None,
        policy: UploadPolicy = DEFAULT_UPLOAD_POLICY,
    ) -> None:
        self._repository = repository
        self._parsers = parsers
        self._validator = validator
        self._policy = policy

    def upload(
        self,
        filename: str,
        content: bytes,
        contract_id: Optional[str] = None,
    ) -> Contract:
        file_format = validate_upload(
            filename, content, self._validator, policy=self._policy
        )

        sha256 = hashlib.sha256(content).hexdigest()
        existing = self._repository.find_by_sha256(sha256)
        if existing is not None:
            raise DuplicateContractError(
                f"文件已存在：contract_id={existing.contract_id}，sha256={sha256}"
            )

        contract = Contract(
            contract_id=contract_id or uuid.uuid4().hex,
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

    def find_by_sha256(self, sha256: str) -> Optional[Contract]:
        return self._repository.find_by_sha256(sha256)

    def delete(self, contract_id: str) -> None:
        self._repository.delete(contract_id)

    def recover_interrupted_parsing(self) -> int:
        return self._repository.reset_parsing_to_failed(
            "服务重启导致解析中断"
        )

    def parsed_artifact(self, contract_id: str) -> tuple[str, bytes]:
        contract = self._parsed_contract(contract_id)
        if contract.parsed_path is None:
            raise InvalidStateError(f"解析产物路径缺失：contract_id={contract_id}")
        return contract.filename, self._repository.read_artifact(contract.parsed_path)

    def cleaned_artifact(self, contract_id: str) -> tuple[str, bytes]:
        contract = self._parsed_contract(contract_id)
        if contract.cleaned_path is None:
            raise InvalidStateError(f"清洗产物路径缺失：contract_id={contract_id}")
        return contract.filename, self._repository.read_artifact(contract.cleaned_path)

    def _parsed_contract(self, contract_id: str) -> Contract:
        contract = self.get(contract_id)
        if contract.status != "parsed":
            raise InvalidStateError(
                f"合同尚未解析完成，无法下载产物：contract_id={contract_id}，status={contract.status}"
            )
        return contract
