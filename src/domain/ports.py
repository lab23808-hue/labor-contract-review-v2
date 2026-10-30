from typing import Optional, Protocol

from core.contract import Contract


class UploadSinkError(Exception):
    pass


class DuplicateContractError(UploadSinkError):
    pass


class UploadSink(Protocol):
    def save(self, contract: Contract, content: bytes) -> None: ...

    def get(self, contract_id: str) -> Optional[Contract]: ...

    def exists_by_sha256(self, sha256: str) -> bool: ...
