import logging
import sqlite3
from contextlib import closing
from datetime import datetime
from pathlib import Path
from typing import Optional, Union

from config import settings
from core.contract import Contract
from domain.ports import DuplicateContractError

logger = logging.getLogger(__name__)

_DUPLICATE_ERRORS = {"SQLITE_CONSTRAINT_PRIMARYKEY", "SQLITE_CONSTRAINT_UNIQUE"}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS contracts (
    sha256 TEXT PRIMARY KEY,
    contract_id TEXT NOT NULL UNIQUE,
    filename TEXT NOT NULL,
    file_format TEXT NOT NULL,
    size_bytes INTEGER NOT NULL CHECK (size_bytes > 0),
    status TEXT NOT NULL CHECK (status IN ('uploaded')),
    error TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_contracts_created_at
    ON contracts(created_at DESC, sha256 ASC);
PRAGMA user_version = 1;
"""


class SQLiteContractRepository:
    def __init__(self, data_dir: Optional[Union[str, Path]] = None) -> None:
        self.data_dir = Path(data_dir if data_dir is not None else settings.DATA_DIR)
        self.db_path = self.data_dir / "metadata.db"
        self.files_dir = self.data_dir / "files"
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.files_dir.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as conn:
            conn.executescript(_SCHEMA)
            conn.commit()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=5)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode = WAL")
        return conn

    def content_path(self, contract: Contract) -> Path:
        return self.files_dir / contract.sha256[:12] / contract.filename

    def save(self, contract: Contract, content: bytes) -> None:
        with closing(self._connect()) as conn:
            try:
                conn.execute(
                    """
                    INSERT INTO contracts (
                        sha256, contract_id, filename, file_format,
                        size_bytes, status, error, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        contract.sha256,
                        contract.contract_id,
                        contract.filename,
                        contract.file_format,
                        contract.size_bytes,
                        contract.status,
                        contract.error,
                        contract.created_at.isoformat(),
                    ),
                )
                conn.commit()
            except sqlite3.IntegrityError as exc:
                conn.rollback()
                if getattr(exc, "sqlite_errorname", "") not in _DUPLICATE_ERRORS:
                    raise
                raise DuplicateContractError(
                    f"文件已存在：sha256={contract.sha256}"
                ) from exc

        path = self.content_path(contract)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
        except OSError:
            with closing(self._connect()) as conn:
                conn.execute(
                    "DELETE FROM contracts WHERE sha256 = ?", (contract.sha256,)
                )
                conn.commit()
            raise

    def get(self, contract_id: str) -> Optional[Contract]:
        with closing(self._connect()) as conn:
            row = conn.execute(
                "SELECT * FROM contracts WHERE contract_id = ?", (contract_id,)
            ).fetchone()
        if row is None:
            return None
        contract = _row_to_contract(row)
        if not self.content_path(contract).exists():
            logger.warning(
                "元数据存在但落盘文件缺失：contract_id=%s sha256=%s path=%s",
                contract.contract_id,
                contract.sha256,
                self.content_path(contract),
            )
            return None
        return contract

    def exists_by_sha256(self, sha256: str) -> bool:
        with closing(self._connect()) as conn:
            row = conn.execute(
                "SELECT 1 FROM contracts WHERE sha256 = ?", (sha256,)
            ).fetchone()
        return row is not None

    def list(self, limit: int, offset: int) -> list[Contract]:
        with closing(self._connect()) as conn:
            rows = conn.execute(
                """
                SELECT * FROM contracts
                ORDER BY created_at DESC, sha256 ASC
                LIMIT ? OFFSET ?
                """,
                (limit, offset),
            ).fetchall()
        return [_row_to_contract(row) for row in rows]


def _row_to_contract(row: sqlite3.Row) -> Contract:
    return Contract(
        contract_id=row["contract_id"],
        filename=row["filename"],
        file_format=row["file_format"],
        sha256=row["sha256"],
        size_bytes=row["size_bytes"],
        status=row["status"],
        error=row["error"],
        created_at=datetime.fromisoformat(row["created_at"]),
    )
