import dataclasses
from typing import Optional

from core.contract import Contract
from core.errors import (
    ArtifactMissingError,
    ContractNotFoundError,
    InvalidStateError,
)
from core.review_task import ReviewTask
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

    def claim_for_parsing(self, contract_id: str) -> bool:
        contract = self._contracts.get(contract_id)
        if contract is None or contract.status == "parsing":
            return False
        self._contracts[contract_id] = contract.model_copy(
            update={"status": "parsing", "error": None}
        )
        return True

    def delete(self, contract_id: str) -> None:
        contract = self._contracts.get(contract_id)
        if contract is None:
            raise ContractNotFoundError(f"合同不存在：{contract_id}")
        if contract.status == "parsing":
            raise InvalidStateError(f"合同正在解析中，无法删除：{contract_id}")
        del self._contracts[contract_id]
        self._by_sha256.pop(contract.sha256, None)
        self.contents.pop(contract_id, None)
        self.artifacts.pop(contract_id, None)
        stale = [
            path for path in self.artifact_files if contract_id in path
        ]
        for path in stale:
            del self.artifact_files[path]

    def reset_parsing_to_failed(self) -> int:
        count = 0
        for contract_id, contract in list(self._contracts.items()):
            if contract.status == "parsing":
                self._contracts[contract_id] = contract.model_copy(
                    update={"status": "failed", "error": "服务重启导致解析中断"}
                )
                count += 1
        return count


class FakeReviewTaskRepository:
    path = ":memory:"

    def __init__(self) -> None:
        self._tasks: dict[str, ReviewTask] = {}
        self._expired: set[str] = set()

    def create(
        self,
        contract_id: str,
        filename: str,
        cleaned_text: Optional[str],
        now: float,
    ) -> None:
        self._tasks[contract_id] = ReviewTask(
            contract_id=contract_id,
            filename=filename,
            status="queued",
            result=None,
            error=None,
            cleaned_text=cleaned_text,
            created_at=now,
            updated_at=now,
        )

    def set_cleaned_text(
        self, contract_id: str, cleaned_text: str, now: float
    ) -> None:
        self._replace(
            contract_id,
            status="queued",
            cleaned_text=cleaned_text,
            updated_at=now,
        )

    def mark_processing(self, contract_id: str, now: float) -> None:
        self._replace(contract_id, status="processing", updated_at=now)

    def mark_queued(self, contract_id: str, now: float) -> None:
        self._replace(contract_id, status="queued", updated_at=now)

    def mark_done(
        self, contract_id: str, result: dict, now: float
    ) -> None:
        self._replace(
            contract_id,
            status="done",
            result=result,
            error=None,
            cleaned_text=None,
            updated_at=now,
        )

    def mark_failed(
        self, contract_id: str, error: str, now: float
    ) -> None:
        self._replace(
            contract_id,
            status="failed",
            result=None,
            error=error,
            cleaned_text=None,
            updated_at=now,
        )

    def delete(self, contract_id: str) -> None:
        self._tasks.pop(contract_id, None)

    def get(self, contract_id: str) -> Optional[ReviewTask]:
        return self._tasks.get(contract_id)

    def list_all(self) -> list[ReviewTask]:
        return sorted(
            self._tasks.values(), key=lambda task: task.created_at
        )

    def delete_expired(self, cutoff: float, now: float) -> list[str]:
        expired = [
            task.contract_id
            for task in self._tasks.values()
            if task.status in ("done", "failed")
            and task.updated_at < cutoff
        ]
        for contract_id in expired:
            self._tasks.pop(contract_id, None)
            self._expired.add(contract_id)
        return expired

    def is_expired(self, contract_id: str) -> bool:
        return contract_id in self._expired

    def expired_count(self) -> int:
        return len(self._expired)

    def close(self) -> None:
        pass

    def _replace(self, contract_id: str, **changes) -> None:
        self._tasks[contract_id] = dataclasses.replace(
            self._tasks[contract_id], **changes
        )
