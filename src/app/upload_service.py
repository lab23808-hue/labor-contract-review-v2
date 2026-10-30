import hashlib
import uuid
from datetime import datetime, timezone

from core.contract import Contract
from core.errors import ContractNotFoundError
from domain.ports import DuplicateContractError, UploadSink
from domain.validation import validate_upload


class UploadService:
    def __init__(self, sink: UploadSink) -> None:
        self._sink = sink

    def upload(self, filename: str, content: bytes) -> Contract:
        file_format = validate_upload(filename, content)

        sha256 = hashlib.sha256(content).hexdigest()
        if self._sink.exists_by_sha256(sha256):
            raise DuplicateContractError(f"文件已存在：sha256={sha256}")

        contract = Contract(
            contract_id=uuid.uuid4().hex,
            filename=filename,
            file_format=file_format,
            sha256=sha256,
            size_bytes=len(content),
            created_at=datetime.now(timezone.utc),
        )
        self._sink.save(contract, content)
        return contract

    def get(self, contract_id: str) -> Contract:
        contract = self._sink.get(contract_id)
        if contract is None:
            raise ContractNotFoundError(f"合同不存在：{contract_id}")
        return contract
