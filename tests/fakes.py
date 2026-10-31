from typing import Optional

from core.contract import Contract
from domain.ports import DuplicateContractError, StoredArtifacts


class InMemoryContractRepository:
    def __init__(self) -> None:
        self._contracts: dict[str, Contract] = {}
        self._by_sha256: dict[str, str] = {}
        self.contents: dict[str, bytes] = {}
        self.artifacts: dict[str, tuple[bytes, bytes]] = {}

    def save(self, contract: Contract, content: bytes) -> None:
        if contract.sha256 in self._by_sha256:
            raise DuplicateContractError(f"文件已存在：sha256={contract.sha256}")
        self._contracts[contract.contract_id] = contract
        self._by_sha256[contract.sha256] = contract.contract_id
        self.contents[contract.contract_id] = content

    def get(self, contract_id: str) -> Optional[Contract]:
        return self._contracts.get(contract_id)

    def exists_by_sha256(self, sha256: str) -> bool:
        return sha256 in self._by_sha256

    def list(self, limit: int, offset: int) -> list[Contract]:
        ordered = sorted(self._contracts.values(), key=lambda contract: contract.sha256)
        ordered.sort(key=lambda contract: contract.created_at, reverse=True)
        return ordered[offset : offset + limit]

    def update(self, contract: Contract) -> None:
        self._contracts[contract.contract_id] = contract

    def read_content(self, contract: Contract) -> bytes:
        return self.contents[contract.contract_id]

    def write_artifacts(
        self, contract: Contract, parsed_markdown: bytes, cleaned_text: bytes
    ) -> StoredArtifacts:
        self.artifacts[contract.contract_id] = (parsed_markdown, cleaned_text)
        return StoredArtifacts(
            parsed_path=f"memory/{contract.contract_id}/parsed.md",
            cleaned_path=f"memory/{contract.contract_id}/cleaned.txt",
        )

    def remove_artifacts(self, contract: Contract) -> None:
        self.artifacts.pop(contract.contract_id, None)
