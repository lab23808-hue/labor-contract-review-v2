import pytest
from fastapi.testclient import TestClient

from api.app import create_app
from infra.sqlite_repository import SQLiteContractRepository

PDF = b"%PDF-1.7\nhello"


@pytest.fixture()
def client(tmp_path):
    app = create_app(repository=SQLiteContractRepository(tmp_path), parsers={})
    with TestClient(app) as http:
        yield http


def _post_pdf(client: TestClient, filename: str = "contract.pdf"):
    return client.post(
        "/contracts", files={"file": (filename, PDF, "application/pdf")}
    )


def test_post_contract_returns_201_with_all_fields(client):
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
    assert body["parsed_path"] is None


def test_post_duplicate_409_and_invalid_422(client):
    assert _post_pdf(client).status_code == 201
    duplicate = _post_pdf(client, filename="again.pdf")
    assert duplicate.status_code == 409
    assert "sha256" in duplicate.json()["detail"]

    invalid = client.post(
        "/contracts",
        files={"file": ("malware.exe", b"contents", "application/octet-stream")},
    )
    assert invalid.status_code == 422
    assert ".exe" in invalid.json()["detail"]


def test_get_existing_and_missing_contract(client):
    created = _post_pdf(client).json()

    response = client.get(f"/contracts/{created['contract_id']}")

    assert response.status_code == 200
    assert response.json() == created
    assert client.get("/contracts/does-not-exist").status_code == 404


def test_list_pagination_and_invalid_params(client):
    created = []
    for index in range(3):
        response = client.post(
            "/contracts",
            files={
                "file": (
                    f"contract-{index}.txt",
                    f"content-{index}".encode(),
                    "text/plain",
                )
            },
        )
        assert response.status_code == 201
        created.append(response.json()["contract_id"])

    assert len(client.get("/contracts").json()) == 3
    page1 = client.get("/contracts", params={"limit": 2}).json()
    page2 = client.get("/contracts", params={"limit": 2, "offset": 2}).json()
    assert len(page1) == 2
    assert len(page2) == 1
    assert {item["contract_id"] for item in page1 + page2} == set(created)

    for params in ({"limit": 101}, {"limit": 0}, {"offset": -1}):
        assert client.get("/contracts", params=params).status_code == 422


def test_restart_keeps_contracts_visible(tmp_path):
    with TestClient(create_app(repository=SQLiteContractRepository(tmp_path))) as first:
        created = _post_pdf(first).json()

    with TestClient(create_app(repository=SQLiteContractRepository(tmp_path))) as reopened:
        assert reopened.get(f"/contracts/{created['contract_id']}").status_code == 200
        listed = reopened.get("/contracts").json()
        assert [item["contract_id"] for item in listed] == [created["contract_id"]]
