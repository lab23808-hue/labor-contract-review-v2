from dataclasses import dataclass
from typing import Any, Optional, Protocol

from core.contract import Contract
from core.review_task import ReviewTask


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


class DocumentValidator(Protocol):
    def validate_pdf(self, content: bytes) -> None: ...


class RateLimiter(Protocol):
    def check(self, key: str) -> tuple[bool, int, int]: ...


class LLMClient(Protocol):
    def call_llm(self, prompt: str) -> dict[str, Any]: ...


class TraceSink(Protocol):
    def write_json(self, name: str, payload: Any) -> None: ...

    def write_text(self, name: str, text: str) -> None: ...

    def log(self, message: str) -> None: ...


class ReviewTaskRepository(Protocol):
    path: str

    def create(
        self,
        contract_id: str,
        filename: str,
        cleaned_text: Optional[str],
        now: float,
    ) -> None: ...

    def set_cleaned_text(
        self, contract_id: str, cleaned_text: str, now: float
    ) -> None: ...

    def mark_processing(self, contract_id: str, now: float) -> None: ...

    def mark_queued(self, contract_id: str, now: float) -> None: ...

    def mark_done(
        self, contract_id: str, result: dict[str, Any], now: float
    ) -> None: ...

    def mark_failed(
        self, contract_id: str, error: str, now: float
    ) -> None: ...

    def delete(self, contract_id: str) -> None: ...

    def get(self, contract_id: str) -> Optional[ReviewTask]: ...

    def list_all(self) -> list[ReviewTask]: ...

    def delete_expired(
        self, cutoff: float, now: float
    ) -> list[str]: ...

    def is_expired(self, contract_id: str) -> bool: ...

    def expired_count(self) -> int: ...

    def close(self) -> None: ...


@dataclass(frozen=True)
class StoredArtifacts:
    parsed_path: str
    cleaned_path: str


class ContractRepository(Protocol):
    def save(self, contract: Contract, content: bytes) -> None: ...

    def get(self, contract_id: str) -> Optional[Contract]: ...

    def exists_by_sha256(self, sha256: str) -> bool: ...

    def find_by_sha256(self, sha256: str) -> Optional[Contract]: ...

    def list(self, limit: int, offset: int) -> list[Contract]: ...

    def update(self, contract: Contract) -> None: ...

    def read_content(self, contract: Contract) -> bytes: ...

    def write_artifacts(
        self, contract: Contract, parsed_markdown: bytes, cleaned_text: bytes
    ) -> StoredArtifacts: ...

    def remove_artifacts(self, contract: Contract) -> None: ...

    def read_artifact(self, relative_path: str) -> bytes: ...

    def claim_for_parsing(self, contract_id: str) -> bool: ...

    def delete(self, contract_id: str) -> None: ...

    def reset_parsing_to_failed(self) -> int: ...
