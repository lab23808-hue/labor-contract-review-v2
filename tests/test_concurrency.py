import hashlib
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from fastapi.testclient import TestClient

from api.app import create_app
from app.contract_manager import ContractManager
from core.contract import Contract
from domain.ports import DuplicateContractError
from infra.llm_client import FakeLLM
from infra.sqlite_repository import SQLiteContractRepository

TXT = b"same content"


def _contract(content: bytes) -> Contract:
    return Contract(
        contract_id=uuid.uuid4().hex,
        filename="contract.txt",
        file_format="txt",
        sha256=hashlib.sha256(content).hexdigest(),
        size_bytes=len(content),
        created_at=datetime.now(timezone.utc),
    )


class BlockingParser:
    def __init__(self) -> None:
        self.started = threading.Event()
        self.release = threading.Event()

    def parse(self, filename, content):
        self.started.set()
        self.release.wait(timeout=10)
        return "# 正文"


def test_concurrent_save_allows_exactly_one(tmp_path):
    repository = SQLiteContractRepository(tmp_path)
    contracts = [_contract(TXT), _contract(TXT)]
    barrier = threading.Barrier(2)
    outcomes = {}

    def attempt(contract):
        barrier.wait()
        try:
            repository.save(contract, TXT)
            outcomes[contract.contract_id] = "saved"
        except DuplicateContractError as exc:
            outcomes[contract.contract_id] = exc

    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(attempt, contracts))

    saved = [cid for cid, result in outcomes.items() if result == "saved"]
    duplicates = [
        result for result in outcomes.values() if isinstance(result, DuplicateContractError)
    ]
    assert len(saved) == 1
    assert len(duplicates) == 1
    assert saved[0] in str(duplicates[0])


def test_concurrent_upload_api_returns_201_and_409(tmp_path):
    app = create_app(
        repository=SQLiteContractRepository(tmp_path),
        parsers={},
        review_client=FakeLLM(
            {"risks": [], "other_observations": []}
        ),
        review_store_path=str(tmp_path / "review.sqlite"),
    )
    with TestClient(app) as client:
        barrier = threading.Barrier(2)

        def post():
            barrier.wait()
            return client.post(
                "/contracts", files={"file": ("same.txt", TXT, "text/plain")}
            )

        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(pool.map(lambda _: post(), range(2)))

    codes = sorted(response.status_code for response in responses)
    assert codes == [201, 409]
    created = next(response for response in responses if response.status_code == 201).json()
    conflict = next(response for response in responses if response.status_code == 409).json()
    assert created["contract_id"] in conflict["detail"]


def test_concurrent_parse_api_returns_202_and_409(tmp_path):
    parser = BlockingParser()
    app = create_app(
        repository=SQLiteContractRepository(tmp_path),
        parsers={"txt": parser},
        review_client=FakeLLM(
            {"risks": [], "other_observations": []}
        ),
        review_store_path=str(tmp_path / "review.sqlite"),
    )
    with TestClient(app) as client:
        manager = ContractManager(
            app.state.contract_repository, app.state.parsers
        )
        created = manager.upload("race.txt", TXT)
        barrier = threading.Barrier(2)

        def parse():
            barrier.wait()
            return client.post(f"/contracts/{created.contract_id}/parse")

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(parse) for _ in range(2)]
            assert parser.started.wait(timeout=5)
            time.sleep(0.2)
            parser.release.set()
            responses = [future.result(timeout=10) for future in futures]

    codes = sorted(response.status_code for response in responses)
    assert codes == [202, 409]
