import logging
import shutil
import sqlite3
from contextlib import closing
from datetime import datetime
from pathlib import Path
from typing import Collection, Optional, Union

from config import settings
from core.contract import Contract
from core.errors import (
    ArtifactMissingError,
    ContractNotFoundError,
    InvalidStateError,
    StorageDeleteError,
)
from domain.ports import DuplicateContractError, StoredArtifacts

logger = logging.getLogger(__name__)


class UnsupportedSchemaVersionError(Exception):
    pass

_DUPLICATE_ERRORS = {"SQLITE_CONSTRAINT_PRIMARYKEY", "SQLITE_CONSTRAINT_UNIQUE"}
_SCHEMA_VERSION = 2

_SCHEMA = """
CREATE TABLE IF NOT EXISTS contracts (
    sha256 TEXT PRIMARY KEY,
    contract_id TEXT NOT NULL UNIQUE,
    filename TEXT NOT NULL,
    file_format TEXT NOT NULL,
    size_bytes INTEGER NOT NULL CHECK (size_bytes > 0),
    status TEXT NOT NULL CHECK (status IN ('uploaded', 'parsing', 'parsed', 'failed')),
    error TEXT,
    created_at TEXT NOT NULL,
    parsed_path TEXT,
    parsed_sha256 TEXT,
    cleaned_path TEXT,
    cleaned_sha256 TEXT,
    parsed_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_contracts_created_at
    ON contracts(created_at DESC, sha256 ASC);
"""


class SQLiteContractRepository:
    def __init__(self, data_dir: Optional[Union[str, Path]] = None) -> None:
        self.data_dir = Path(data_dir if data_dir is not None else settings.DATA_DIR)
        self.db_path = self.data_dir / "metadata.db"
        self.files_dir = self.data_dir / "files"
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.files_dir.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as conn:
            self._migrate(conn)
            conn.commit()
        self._migrate_directory_layout()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=5)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode = WAL")
        return conn

    def _migrate(self, conn: sqlite3.Connection) -> None:
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        if version == _SCHEMA_VERSION:
            return
        if version == 0:
            conn.executescript(_SCHEMA)
            conn.execute(f"PRAGMA user_version = {_SCHEMA_VERSION}")
            return
        if version == 1:
            self._migrate_v1_to_v2(conn)
            return
        raise UnsupportedSchemaVersionError(f"不支持的数据库版本：{version}")

    @staticmethod
    def _migrate_v1_to_v2(conn: sqlite3.Connection) -> None:
        conn.execute("DROP INDEX IF EXISTS idx_contracts_created_at")
        conn.execute("ALTER TABLE contracts RENAME TO contracts_v1")
        conn.executescript(_SCHEMA)
        conn.execute(
            """
            INSERT INTO contracts (
                sha256, contract_id, filename, file_format,
                size_bytes, status, error, created_at
            )
            SELECT sha256, contract_id, filename, file_format,
                   size_bytes, status, error, created_at
            FROM contracts_v1
            """
        )
        conn.execute("DROP TABLE contracts_v1")
        conn.execute(f"PRAGMA user_version = {_SCHEMA_VERSION}")

    def _migrate_directory_layout(self) -> None:
        with closing(self._connect()) as conn:
            rows = conn.execute("SELECT sha256 FROM contracts").fetchall()
        for row in rows:
            sha256 = row["sha256"]
            old_dir = self.files_dir / sha256[:12]
            new_dir = self.files_dir / sha256
            if old_dir.is_dir() and not new_dir.exists():
                old_dir.rename(new_dir)
                logger.info("落盘目录迁移完成：%s -> %s", old_dir, new_dir)
            elif old_dir.is_dir():
                logger.warning(
                    "新旧落盘目录并存，保留新目录：old=%s new=%s", old_dir, new_dir
                )
            elif not new_dir.exists():
                logger.warning("落盘目录缺失：sha256=%s", sha256)

    def content_path(self, contract: Contract) -> Path:
        return self.files_dir / contract.sha256 / contract.filename

    def artifacts_dir(self, contract: Contract) -> Path:
        return self.files_dir / contract.sha256 / "artifacts"

    def _relative(self, path: Path) -> str:
        return path.relative_to(self.data_dir).as_posix()

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
                existing = conn.execute(
                    "SELECT contract_id FROM contracts WHERE sha256 = ?",
                    (contract.sha256,),
                ).fetchone()
                existing_id = existing["contract_id"] if existing else "unknown"
                raise DuplicateContractError(
                    f"文件已存在：contract_id={existing_id}，sha256={contract.sha256}"
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

    def find_by_sha256(self, sha256: str) -> Optional[Contract]:
        with closing(self._connect()) as conn:
            row = conn.execute(
                "SELECT * FROM contracts WHERE sha256 = ?", (sha256,)
            ).fetchone()
        return _row_to_contract(row) if row is not None else None

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

    def update(self, contract: Contract) -> None:
        with closing(self._connect()) as conn:
            conn.execute(
                """
                UPDATE contracts SET
                    status = ?, error = ?, parsed_path = ?, parsed_sha256 = ?,
                    cleaned_path = ?, cleaned_sha256 = ?, parsed_at = ?
                WHERE contract_id = ?
                """,
                (
                    contract.status,
                    contract.error,
                    contract.parsed_path,
                    contract.parsed_sha256,
                    contract.cleaned_path,
                    contract.cleaned_sha256,
                    contract.parsed_at.isoformat() if contract.parsed_at else None,
                    contract.contract_id,
                ),
            )
            conn.commit()

    def read_content(self, contract: Contract) -> bytes:
        return self.content_path(contract).read_bytes()

    def write_artifacts(
        self, contract: Contract, parsed_markdown: bytes, cleaned_text: bytes
    ) -> StoredArtifacts:
        directory = self.artifacts_dir(contract)
        directory.mkdir(parents=True, exist_ok=True)
        parsed_path = directory / "parsed.md"
        cleaned_path = directory / "cleaned.txt"
        parsed_path.write_bytes(parsed_markdown)
        cleaned_path.write_bytes(cleaned_text)
        return StoredArtifacts(
            parsed_path=self._relative(parsed_path),
            cleaned_path=self._relative(cleaned_path),
        )

    def remove_artifacts(self, contract: Contract) -> None:
        directory = self.artifacts_dir(contract)
        if not directory.is_dir():
            return
        for name in ("parsed.md", "cleaned.txt"):
            (directory / name).unlink(missing_ok=True)
        if not any(directory.iterdir()):
            directory.rmdir()

    def read_artifact(self, relative_path: str) -> bytes:
        path = (self.data_dir / relative_path).resolve()
        root = self.data_dir.resolve()
        if path != root and root not in path.parents:
            raise ArtifactMissingError(f"artifacts 路径非法：{relative_path}")
        if not path.is_file():
            raise ArtifactMissingError(f"artifacts 丢失：{relative_path}")
        return path.read_bytes()

    def claim_for_parsing(
        self, contract_id: str, allowed_statuses: Collection[str]
    ) -> bool:
        statuses = tuple(allowed_statuses)
        if not statuses:
            return False
        placeholders = ",".join("?" for _ in statuses)
        with closing(self._connect()) as conn:
            cursor = conn.execute(
                f"""
                UPDATE contracts
                SET status = 'parsing', error = NULL
                WHERE contract_id = ?
                  AND status IN ({placeholders})
                """,
                (contract_id, *statuses),
            )
            conn.commit()
            return cursor.rowcount == 1

    def delete(self, contract_id: str) -> None:
        conn = self._connect()
        try:
            # BEGIN IMMEDIATE 先取得写锁：删除期间 parse 的原子抢占会等待，
            # 避免“一边解析一边删目录”。
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM contracts WHERE contract_id = ?", (contract_id,)
            ).fetchone()
            if row is None:
                raise ContractNotFoundError(f"合同不存在：{contract_id}")
            contract = _row_to_contract(row)
            if contract.status == "parsing":
                raise InvalidStateError(f"合同正在解析中，无法删除：{contract_id}")

            directory = (self.files_dir / contract.sha256).resolve()
            root = self.files_dir.resolve()
            if directory == root or root not in directory.parents:
                raise ArtifactMissingError(f"落盘路径非法：{contract.sha256}")

            # 先删磁盘、后删账本：磁盘删除不可回滚。磁盘删除失败时回滚事务、
            # 保留账本行（宁可账上有账、仓库缺房），避免留下无账本的孤儿目录；
            # 若磁盘已删而账本删除失败，则提交前抛错回滚，账行保留，属于同类取舍。
            try:
                if directory.is_dir():
                    shutil.rmtree(directory)
                else:
                    logger.warning(
                        "删除时落盘目录已缺失：contract_id=%s sha256=%s",
                        contract.contract_id,
                        contract.sha256,
                    )
            except OSError as exc:
                raise StorageDeleteError(f"删除落盘目录失败：{exc}") from exc

            conn.execute(
                "DELETE FROM contracts WHERE contract_id = ?", (contract_id,)
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def reset_parsing_to_failed(self, reason: str) -> int:
        """把 status='parsing' 的记录改为 failed，error 记入调用方给出的原因。返回影响行数。"""
        with closing(self._connect()) as conn:
            cursor = conn.execute(
                "UPDATE contracts SET status = 'failed', error = ? WHERE status = 'parsing'",
                (reason,),
            )
            conn.commit()
            rowcount = cursor.rowcount
        if rowcount:
            logger.warning("启动回收：%d 条 parsing 记录转为 failed", rowcount)
        return rowcount


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
        parsed_path=row["parsed_path"],
        parsed_sha256=row["parsed_sha256"],
        cleaned_path=row["cleaned_path"],
        cleaned_sha256=row["cleaned_sha256"],
        parsed_at=(
            datetime.fromisoformat(row["parsed_at"]) if row["parsed_at"] else None
        ),
    )
