import tempfile
import threading
from pathlib import Path
from typing import Annotated, Optional

from fastapi import Depends, Request

from app.upload_service import UploadService
from core.contract import Contract
from domain.ports import DuplicateContractError, UploadSink


class TempFileUploadSink:
    """临时实现：M2 决定正式落盘布局后整体替换。"""

    def __init__(self, root: Optional[Path] = None) -> None:
        self.root = (
            Path(root)
            if root is not None
            else Path(tempfile.gettempdir()) / "labor_contract_review_v2_uploads"
        )
        self._index: dict[str, str] = {}
        self._lock = threading.Lock()

    def _contract_dir(self, sha256: str) -> Path:
        return self.root / sha256[:12]

    def save(self, contract: Contract, content: bytes) -> None:
        with self._lock:
            if contract.sha256 in self._index:
                raise DuplicateContractError(f"文件已存在：sha256={contract.sha256}")

            directory = self._contract_dir(contract.sha256)
            directory.mkdir(parents=True, exist_ok=True)
            (directory / f"original.{contract.file_format}").write_bytes(content)
            (directory / "metadata.json").write_text(
                contract.model_dump_json(indent=2), encoding="utf-8"
            )
            self._index[contract.sha256] = contract.contract_id

    def get(self, contract_id: str) -> Optional[Contract]:
        with self._lock:
            sha256 = next(
                (sha for sha, cid in self._index.items() if cid == contract_id), None
            )
        if sha256 is None:
            return None
        metadata = self._contract_dir(sha256) / "metadata.json"
        if not metadata.exists():
            return None
        return Contract.model_validate_json(metadata.read_text(encoding="utf-8"))

    def exists_by_sha256(self, sha256: str) -> bool:
        with self._lock:
            return sha256 in self._index


def get_upload_sink(request: Request) -> UploadSink:
    return request.app.state.upload_sink


def get_upload_service(
    sink: Annotated[UploadSink, Depends(get_upload_sink)],
) -> UploadService:
    return UploadService(sink)
