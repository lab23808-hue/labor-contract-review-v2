import pytest
from fastapi.testclient import TestClient

from api.app import create_app
from config import settings
from infra.llm_client import FakeLLM
from infra.sqlite_repository import SQLiteContractRepository
from pdf_fixtures import make_valid_pdf

PDF = make_valid_pdf()
JPG = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01"


@pytest.fixture()
def client(tmp_path):
    app = create_app(
        repository=SQLiteContractRepository(tmp_path),
        parsers={},
        review_client=FakeLLM(
            {"risks": [], "other_observations": []}
        ),
        review_store_path=str(tmp_path / "review.sqlite"),
    )
    with TestClient(app) as http:
        yield http


def _post_pdf(client: TestClient, filename: str = "contract.pdf"):
    return client.post(
        "/contracts", files={"file": (filename, PDF, "application/pdf")}
    )


def test_post_image_with_valid_magic_is_accepted(client):
    response = client.post(
        "/contracts", files={"file": ("scan.jpg", JPG, "image/jpeg")}
    )

    assert response.status_code == 201
    assert response.json()["file_format"] == "jpg"


def test_post_image_with_bad_magic_is_rejected(client):
    response = client.post(
        "/contracts",
        files={"file": ("scan.png", b"not a png", "image/png")},
    )

    assert response.status_code == 422
    assert "文件头" in response.json()["detail"]


def test_upload_at_limit_is_accepted(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "MAX_UPLOAD_BYTES", 16)
    app = create_app(
        repository=SQLiteContractRepository(tmp_path),
        parsers={},
        review_client=FakeLLM(
            {"risks": [], "other_observations": []}
        ),
        review_store_path=str(tmp_path / "review.sqlite"),
    )

    with TestClient(app) as client:
        response = client.post(
            "/contracts",
            files={"file": ("limit.txt", b"a" * 16, "text/plain")},
        )

    assert response.status_code == 201


def test_upload_over_limit_is_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "MAX_UPLOAD_BYTES", 16)
    app = create_app(
        repository=SQLiteContractRepository(tmp_path),
        parsers={},
        review_client=FakeLLM(
            {"risks": [], "other_observations": []}
        ),
        review_store_path=str(tmp_path / "review.sqlite"),
    )

    with TestClient(app) as client:
        response = client.post(
            "/contracts",
            files={"file": ("big.txt", b"a" * 17, "text/plain")},
        )

    assert response.status_code == 422
    assert "上限" in response.json()["detail"]


def test_post_contract_returns_201_with_all_fields(client):
    response = _post_pdf(client)

    assert response.status_code == 201
    body = response.json()
    assert body["filename"] == "contract.pdf"
    assert body["file_format"] == "pdf"
    assert len(body["sha256"]) == 64
    assert body["size_bytes"] == len(PDF)
    assert body["status"] == "parsing"
    assert body["error"] is None
    assert body["contract_id"]
    assert body["created_at"]
    assert body["parsed_path"] is None


def test_post_duplicate_409_and_invalid_422(client):
    created = _post_pdf(client).json()
    assert created["contract_id"]

    duplicate = _post_pdf(client, filename="again.pdf")

    assert duplicate.status_code == 409
    detail = duplicate.json()["detail"]
    assert "sha256" in detail
    assert created["contract_id"] in detail

    invalid = client.post(
        "/contracts",
        files={"file": ("malware.exe", b"contents", "application/octet-stream")},
    )
    assert invalid.status_code == 422
    assert ".exe" in invalid.json()["detail"]

    corrupted = client.post(
        "/contracts",
        files={"file": ("broken.pdf", b"%PDF-1.7\nbroken body", "application/pdf")},
    )
    assert corrupted.status_code == 400
    assert "PDF" in corrupted.json()["detail"]


def test_get_existing_and_missing_contract(client):
    created = _post_pdf(client).json()

    response = client.get(f"/contracts/{created['contract_id']}")

    assert response.status_code == 200
    body = response.json()
    assert body["contract_id"] == created["contract_id"]
    assert body["filename"] == created["filename"]
    assert body["status"] in ("uploaded", "parsed", "failed")
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
    review_store = str(tmp_path / "review.sqlite")
    with TestClient(
        create_app(
            repository=SQLiteContractRepository(tmp_path),
            parsers={},
            review_client=FakeLLM(
                {"risks": [], "other_observations": []}
            ),
            review_store_path=review_store,
        )
    ) as first:
        created = _post_pdf(first).json()

    with TestClient(
        create_app(
            repository=SQLiteContractRepository(tmp_path),
            parsers={},
            review_client=FakeLLM(
                {"risks": [], "other_observations": []}
            ),
            review_store_path=review_store,
        )
    ) as reopened:
        assert reopened.get(f"/contracts/{created['contract_id']}").status_code == 200
        listed = reopened.get("/contracts").json()
        assert [item["contract_id"] for item in listed] == [created["contract_id"]]
