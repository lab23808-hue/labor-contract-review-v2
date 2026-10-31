from typing import Optional

from core.contract import Contract
from core.errors import ArtifactMissingError
from domain.ports import DuplicateContractError, StoredArtifacts


class InMemoryContractRepository:
    def __init__(self) -> None:
        self._contracts: dict[str, Contract] = {}
        self._by_sha256: dict[str, str] = {}
        self.contents: dict[str, bytes] = {}
        self.artifacts: dict[str, tuple[bytes, bytes]] = {}
        self.artifact_files: dict[str, bytes] = {}

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

    def find_by_sha256(self, sha256: str) -> Optional[Contract]:
        contract_id = self._by_sha256.get(sha256)
        return self._contracts.get(contract_id) if contract_id else None

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
        parsed_path = f"memory/{contract.contract_id}/parsed.md"
        cleaned_path = f"memory/{contract.contract_id}/cleaned.txt"
        self.artifact_files[parsed_path] = parsed_markdown
        self.artifact_files[cleaned_path] = cleaned_text
        return StoredArtifacts(parsed_path=parsed_path, cleaned_path=cleaned_path)

    def remove_artifacts(self, contract: Contract) -> None:
        self.artifacts.pop(contract.contract_id, None)
        stale = [
            path
            for path in self.artifact_files
            if contract.contract_id in path
        ]
        for path in stale:
            del self.artifact_files[path]

    def read_artifact(self, relative_path: str) -> bytes:
        try:
            return self.artifact_files[relative_path]
        except KeyError:
            raise ArtifactMissingError(f"artifacts 丢失：{relative_path}") from None

    def reset_parsing_to_failed(self) -> int:
        count = 0
        for contract_id, contract in list(self._contracts.items()):
            if contract.status == "parsing":
                self._contracts[contract_id] = contract.model_copy(
                    update={"status": "failed", "error": "服务重启导致解析中断"}
                )
                count += 1
        return count
