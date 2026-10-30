from typing import Optional, Protocol

from core.contract import Contract


class UploadSinkError(Exception):
    pass


class DuplicateContractError(UploadSinkError):
    pass


class ContractRepository(Protocol):
    def save(self, contract: Contract, content: bytes) -> None: ...

    def get(self, contract_id: str) -> Optional[Contract]: ...

    def exists_by_sha256(self, sha256: str) -> bool: ...

    def list(self, limit: int, offset: int) -> list[Contract]: ...
