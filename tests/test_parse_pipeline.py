import asyncio
import hashlib
import sqlite3
import threading
import time
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from api.app import create_app
from app.contract_manager import ContractManager
from app.parsing_service import ParsingService
from app.upload_pipeline import UploadPipeline
from domain.ports import ParsingError
from fakes import InMemoryContractRepository
from infra.llm_client import FakeLLM
from infra.sqlite_repository import SQLiteContractRepository
from infra.txt_parser import TxtParser
from infra.upload_validator import FileUploadValidator

TXT = "劳动合同\n\n第一条　试用期三个月。\n第 1 页\n".encode("utf-8")


class ScriptedParser:
    def __init__(self) -> None:
        self.fail = False

    def parse(self, filename, content):
        if self.fail:
            raise ParsingError("解析失败")
        return "# 标题\n\n正文"


@pytest.fixture()
def parse_client(tmp_path):
    parsers = {"txt": TxtParser()}
    app = create_app(
        repository=SQLiteContractRepository(tmp_path),
        parsers=parsers,
        review_client=FakeLLM({"risks": [], "other_observations": []}),
        review_store_path=tmp_path / "review_tasks.sqlite",
    )
    with TestClient(app) as client:
        yield client, tmp_path


def _upload(client, filename="contract.txt", content=TXT):
    response = client.post(
        "/contracts", files={"file": (filename, content, "application/octet-stream")}
    )
    assert response.status_code == 201
    return response.json()


def _wait_for_final(client, contract_id, timeout=5.0):
    deadline = time.monotonic() + timeout
    body = client.get(f"/contracts/{contract_id}").json()
    while time.monotonic() < deadline:
        if body["status"] in ("parsed", "failed"):
            return body
        time.sleep(0.02)
        body = client.get(f"/contracts/{contract_id}").json()
    raise AssertionError(f"解析未在 {timeout}s 内完成：{body}")


def test_parse_txt_produces_artifacts(parse_client):
    client, tmp_path = parse_client
    created = _upload(client)

    body = _wait_for_final(client, created["contract_id"])
    assert body["status"] == "parsed"
    assert body["error"] is None
    assert body["parsed_at"]
    parsed_file = tmp_path / body["parsed_path"]
    cleaned_file = tmp_path / body["cleaned_path"]
    assert parsed_file.exists() and cleaned_file.exists()
    assert hashlib.sha256(parsed_file.read_bytes()).hexdigest() == body["parsed_sha256"]
    assert (
        hashlib.sha256(cleaned_file.read_bytes()).hexdigest() == body["cleaned_sha256"]
    )
    cleaned = cleaned_file.read_text(encoding="utf-8")
    assert "第 1 页" not in cleaned
    assert "第一条 试用期三个月。" in cleaned

    parsed_download = client.get(f"/contracts/{created['contract_id']}/parsed")
    cleaned_download = client.get(f"/contracts/{created['contract_id']}/cleaned")
    assert parsed_download.status_code == 200
    assert parsed_download.headers["content-type"].startswith("text/markdown")
    assert "contract.txt" in parsed_download.headers["content-disposition"]
    assert parsed_download.content == parsed_file.read_bytes()
    assert cleaned_download.status_code == 200
    assert cleaned_download.headers["content-type"].startswith("text/plain")
    assert cleaned_download.content == cleaned_file.read_bytes()

    failed = _upload(client, "pending.docx", b"PK\x03\x04pending")
    assert _wait_for_final(client, failed["contract_id"])["status"] == "failed"
    assert client.get(f"/contracts/{failed['contract_id']}/parsed").status_code == 409
    assert client.get(f"/contracts/{failed['contract_id']}/cleaned").status_code == 409

    cleaned_file.unlink()
    missing = client.get(f"/contracts/{created['contract_id']}/cleaned")
    assert missing.status_code == 500
    assert "artifacts 丢失" in missing.json()["detail"]


def test_corrupted_pdf_rejected_at_upload(parse_client):
    client, _ = parse_client

    response = client.post(
        "/contracts",
        files={"file": ("broken.pdf", b"%PDF-1.7\nnot really a pdf", "application/pdf")},
    )

    assert response.status_code == 400
    assert "PDF" in response.json()["detail"]
    assert client.get("/contracts").json() == []


def test_reparse_is_idempotent(parse_client):
    client, _ = parse_client
    created = _upload(client)

    first_response = client.post(f"/contracts/{created['contract_id']}/parse")
    assert first_response.status_code == 202
    first = _wait_for_final(client, created["contract_id"])

    second_response = client.post(f"/contracts/{created['contract_id']}/parse")
    assert second_response.status_code == 202
    second = _wait_for_final(client, created["contract_id"])

    assert first["status"] == second["status"] == "parsed"
    assert first["parsed_path"] == second["parsed_path"]
    assert first["parsed_sha256"] == second["parsed_sha256"]
    assert first["cleaned_sha256"] == second["cleaned_sha256"]


def test_reparse_failure_cleans_previous_artifacts():
    repository = InMemoryContractRepository()
    parser = ScriptedParser()
    manager = ContractManager(repository, {"txt": parser}, FileUploadValidator())
    parsing = ParsingService(repository, {"txt": parser})
    contract = manager.upload("contract.txt", b"first")

    assert parsing.parse(contract.contract_id).status == "parsed"
    assert repository.artifacts

    parser.fail = True
    failed = parsing.parse(contract.contract_id)

    assert failed.status == "failed"
    assert failed.parsed_path is None
    assert failed.error
    assert not repository.artifacts


def test_parse_conflict_and_not_found(parse_client):
    client, tmp_path = parse_client
    created = _upload(client)
    repository = SQLiteContractRepository(tmp_path)
    contract = repository.get(created["contract_id"])
    repository.update(contract.model_copy(update={"status": "parsing"}))

    assert client.post(f"/contracts/{created['contract_id']}/parse").status_code == 409
    assert client.post("/contracts/missing/parse").status_code == 404


def test_parse_tasks_are_serialized():
    class RecordingParsing:
        def __init__(self) -> None:
            self.active = 0
            self.max_active = 0
            self.completed = 0
            self._lock = threading.Lock()

        def execute(self, contract_id: str):
            with self._lock:
                self.active += 1
                self.max_active = max(self.max_active, self.active)
            time.sleep(0.05)
            with self._lock:
                self.active -= 1
                self.completed += 1
            return SimpleNamespace(status="failed", error="test")

    class NoopReview:
        def fail_reserved(self, contract_id, error) -> None:
            pass

        def submit_reserved(self, contract_id, cleaned_text) -> bool:
            return True

    parsing = RecordingParsing()

    async def run_all() -> None:
        pipeline = UploadPipeline(
            contract_manager=None,
            parsing_service=parsing,
            review_service=NoopReview(),
            rate_limiter=None,
            parse_semaphore=asyncio.Semaphore(1),
            max_batch_files=20,
            max_batch_bytes=200 * 1024 * 1024,
        )
        await asyncio.gather(
            *(
                pipeline.process(f"contract-{index}")
                for index in range(3)
            )
        )

    asyncio.run(run_all())

    assert parsing.max_active == 1
    assert parsing.completed == 3


def test_restart_keeps_parsed_pointers(tmp_path):
    repository = SQLiteContractRepository(tmp_path)
    manager = ContractManager(repository, {"txt": TxtParser()}, FileUploadValidator())
    parsing = ParsingService(repository, {"txt": TxtParser()})
    created = manager.upload("contract.txt", b"first line")
    assert parsing.parse(created.contract_id).status == "parsed"

    reopened = SQLiteContractRepository(tmp_path)
    contract = reopened.get(created.contract_id)

    assert contract.status == "parsed"
    assert (tmp_path / contract.parsed_path).exists()
    assert (tmp_path / contract.cleaned_path).exists()


def test_legacy_twelve_char_layout_is_migrated(tmp_path):
    content = b"%PDF-1.7\nlegacy"
    sha256 = hashlib.sha256(content).hexdigest()
    contract_id = uuid.uuid4().hex
    legacy_dir = tmp_path / "files" / sha256[:12]
    legacy_dir.mkdir(parents=True)
    (legacy_dir / "legacy.pdf").write_bytes(content)
    _create_v1_database(tmp_path, contract_id, sha256, "legacy.pdf", len(content))

    repository = SQLiteContractRepository(tmp_path)

    contract = repository.get(contract_id)
    assert contract is not None
    assert (tmp_path / "files" / sha256 / "legacy.pdf").exists()
    assert not legacy_dir.exists()


def _create_v1_database(data_dir, contract_id, sha256, filename, size_bytes):
    conn = sqlite3.connect(data_dir / "metadata.db")
    conn.executescript(
        """
        CREATE TABLE contracts (
            sha256 TEXT PRIMARY KEY,
            contract_id TEXT NOT NULL UNIQUE,
            filename TEXT NOT NULL,
            file_format TEXT NOT NULL,
            size_bytes INTEGER NOT NULL CHECK (size_bytes > 0),
            status TEXT NOT NULL CHECK (status IN ('uploaded')),
            error TEXT,
            created_at TEXT NOT NULL
        );
        CREATE INDEX idx_contracts_created_at
            ON contracts(created_at DESC, sha256 ASC);
        PRAGMA user_version = 1;
        """
    )
    conn.execute(
        "INSERT INTO contracts VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            sha256,
            contract_id,
            filename,
            "pdf",
            size_bytes,
            "uploaded",
            None,
            datetime(2026, 1, 1, tzinfo=timezone.utc).isoformat(),
        ),
    )
    conn.commit()
    conn.close()
