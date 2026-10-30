import json

import pytest
from fastapi.testclient import TestClient

from api.app import create_app
from api.dependencies import TempFileUploadSink

PDF = b"%PDF-1.7\nhello"


@pytest.fixture()
def client_and_sink(tmp_path):
    sink = TempFileUploadSink(root=tmp_path)
    with TestClient(create_app(sink=sink)) as client:
        yield client, sink


def _post_pdf(client: TestClient, filename: str = "contract.pdf"):
    return client.post(
        "/contracts", files={"file": (filename, PDF, "application/pdf")}
    )


def test_post_contract_returns_201_with_all_fields(client_and_sink):
    client, _ = client_and_sink

    response = _post_pdf(client)

    assert response.status_code == 201
    body = response.json()
    assert body["filename"] == "contract.pdf"
    assert body["file_format"] == "pdf"
    assert len(body["sha256"]) == 64
    assert body["size_bytes"] == len(PDF)
    assert body["status"] == "uploaded"
    assert body["error"] is None
    assert body["contract_id"]
    assert body["created_at"]


def test_post_duplicate_returns_409(client_and_sink):
    client, _ = client_and_sink
    assert _post_pdf(client).status_code == 201

    response = _post_pdf(client, filename="again.pdf")

    assert response.status_code == 409
    assert "sha256" in response.json()["detail"]


def test_post_invalid_file_returns_422(client_and_sink):
    client, _ = client_and_sink

    response = client.post(
        "/contracts",
        files={"file": ("malware.exe", b"contents", "application/octet-stream")},
    )

    assert response.status_code == 422
    assert ".exe" in response.json()["detail"]


def test_get_existing_contract_returns_200(client_and_sink):
    client, _ = client_and_sink
    created = _post_pdf(client).json()

    response = client.get(f"/contracts/{created['contract_id']}")

    assert response.status_code == 200
    assert response.json() == created


def test_get_missing_contract_returns_404(client_and_sink):
    client, _ = client_and_sink

    response = client.get("/contracts/does-not-exist")

    assert response.status_code == 404


def test_metadata_json_matches_post_response(client_and_sink):
    client, sink = client_and_sink
    created = _post_pdf(client).json()

    metadata_path = sink.root / created["sha256"][:12] / "metadata.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))

    assert metadata == created
