import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from api.app import create_app
from config import settings
from infra.llm_client import FakeLLM
from infra.sqlite_repository import SQLiteContractRepository
from infra.txt_parser import TxtParser

DUP_CONTENT = b"duplicate batch content"


@pytest.fixture()
def client(tmp_path):
    app = create_app(
        repository=SQLiteContractRepository(tmp_path),
        parsers={"txt": TxtParser()},
        review_client=FakeLLM(
            {"risks": [], "other_observations": []}
        ),
        review_store_path=str(tmp_path / "review.sqlite"),
    )
    with TestClient(app) as http:
        yield http


def test_batch_upload_reports_per_item_status(client):
    first = client.post(
        "/contracts", files={"file": ("dup.txt", DUP_CONTENT, "text/plain")}
    )
    assert first.status_code == 201

    response = client.post(
        "/contracts/batch",
        files=[
            ("files", ("new.txt", b"new content", "text/plain")),
            ("files", ("bad.exe", b"bad", "application/octet-stream")),
            ("files", ("dup.txt", DUP_CONTENT, "text/plain")),
            ("files", ("broken.pdf", b"%PDF-1.7\nbroken body", "application/pdf")),
        ],
    )

    assert response.status_code == 200
    body = response.json()
    assert [item["status"] for item in body["items"]] == [201, 422, 409, 400]
    assert body["items"][0]["detail"] == "uploaded, parse scheduled"
    assert body["items"][0]["contract_id"]
    assert body["items"][1]["contract_id"] is None
    assert body["items"][2]["contract_id"] == first.json()["contract_id"]
    assert body["summary"] == {"total": 4, "succeeded": 1, "failed": 3}

    created_id = body["items"][0]["contract_id"]
    assert client.get(f"/contracts/{created_id}").json()["status"] == "parsed"


def test_batch_rejects_too_many_files(client):
    files = [
        ("files", (f"f{index}.txt", b"x", "text/plain")) for index in range(21)
    ]

    response = client.post("/contracts/batch", files=files)

    assert response.status_code == 422
    assert "20" in response.json()["detail"]


def test_batch_rejects_total_size_over_limit(client, monkeypatch):
    monkeypatch.setattr(settings, "MAX_BATCH_BYTES", 10)

    response = client.post(
        "/contracts/batch",
        files=[
            ("files", ("a.txt", b"123456", "text/plain")),
            ("files", ("b.txt", b"123456", "text/plain")),
        ],
    )

    assert response.status_code == 422
    assert "总大小" in response.json()["detail"]


def test_concurrent_batch_and_single_upload_race(tmp_path):
    content = b"race batch content"
    app = create_app(
        repository=SQLiteContractRepository(tmp_path),
        parsers={"txt": TxtParser()},
        review_client=FakeLLM(
            {"risks": [], "other_observations": []}
        ),
        review_store_path=str(tmp_path / "review.sqlite"),
    )
    with TestClient(app) as http:
        barrier = threading.Barrier(2)

        def post_batch():
            barrier.wait()
            return http.post(
                "/contracts/batch",
                files=[("files", ("race.txt", content, "text/plain"))],
            )

        def post_single():
            barrier.wait()
            return http.post(
                "/contracts", files={"file": ("race.txt", content, "text/plain")}
            )

        with ThreadPoolExecutor(max_workers=2) as pool:
            batch_future = pool.submit(post_batch)
            single_future = pool.submit(post_single)
            batch_response = batch_future.result(timeout=10)
            single_response = single_future.result(timeout=10)

    batch_item = batch_response.json()["items"][0]
    batch_ok = batch_item["status"] == 201
    single_ok = single_response.status_code == 201
    assert batch_ok != single_ok

    if single_ok:
        winner_id = single_response.json()["contract_id"]
        loser_detail = batch_item["detail"]
    else:
        winner_id = batch_item["contract_id"]
        loser_detail = single_response.json()["detail"]
    assert winner_id in loser_detail
