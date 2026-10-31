from dataclasses import dataclass
from typing import Optional, Protocol

from core.contract import Contract


class UploadSinkError(Exception):
    pass


class DuplicateContractError(UploadSinkError):
    pass


class ParsingError(Exception):
    pass


class UnsupportedFormatError(ParsingError):
    pass


class DocumentParser(Protocol):
    def parse(self, filename: str, content: bytes) -> str: ...


@dataclass(frozen=True)
class StoredArtifacts:
    parsed_path: str
    cleaned_path: str


class ContractRepository(Protocol):
    def save(self, contract: Contract, content: bytes) -> None: ...

    def get(self, contract_id: str) -> Optional[Contract]: ...

    def exists_by_sha256(self, sha256: str) -> bool: ...

    def list(self, limit: int, offset: int) -> list[Contract]: ...

    def update(self, contract: Contract) -> None: ...

    def read_content(self, contract: Contract) -> bytes: ...

    def write_artifacts(
        self, contract: Contract, parsed_markdown: bytes, cleaned_text: bytes
    ) -> StoredArtifacts: ...

    def remove_artifacts(self, contract: Contract) -> None: ...

    def reset_parsing_to_failed(self) -> int: ...
