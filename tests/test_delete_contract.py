import pytest
from fastapi.testclient import TestClient

import infra.sqlite_repository as repository_module
from api.app import create_app
from infra.sqlite_repository import SQLiteContractRepository

TXT = b"delete me content"


@pytest.fixture()
def env(tmp_path):
    repository = SQLiteContractRepository(tmp_path)
    app = create_app(repository=repository, parsers={})
    with TestClient(app) as client:
        yield client, repository, tmp_path


def _upload(client, filename="delete-me.txt", content=TXT):
    response = client.post(
        "/contracts", files={"file": (filename, content, "text/plain")}
    )
    assert response.status_code == 201
    return response.json()


def test_delete_contract_removes_row_and_directory(env):
    client, repository, tmp_path = env
    created = _upload(client)
    directory = tmp_path / "files" / created["sha256"]
    assert directory.is_dir()

    response = client.delete(f"/contracts/{created['contract_id']}")

    assert response.status_code == 204
    assert response.content == b""
    assert client.get(f"/contracts/{created['contract_id']}").status_code == 404
    assert repository.get(created["contract_id"]) is None
    assert not directory.exists()


def test_delete_missing_returns_404(env):
    client, _, _ = env

    assert client.delete("/contracts/does-not-exist").status_code == 404


def test_delete_parsing_returns_409_and_keeps_row_and_directory(env):
    client, repository, tmp_path = env
    created = _upload(client)
    contract = repository.get(created["contract_id"])
    repository.update(contract.model_copy(update={"status": "parsing"}))
    directory = tmp_path / "files" / created["sha256"]

    response = client.delete(f"/contracts/{created['contract_id']}")

    assert response.status_code == 409
    assert repository.get(created["contract_id"]).status == "parsing"
    assert directory.is_dir()


def test_delete_disk_failure_keeps_row(env, monkeypatch):
    client, repository, tmp_path = env
    created = _upload(client)
    directory = tmp_path / "files" / created["sha256"]

    def failing_rmtree(path, *args, **kwargs):
        raise OSError("模拟磁盘删除失败")

    monkeypatch.setattr(repository_module.shutil, "rmtree", failing_rmtree)

    response = client.delete(f"/contracts/{created['contract_id']}")

    assert response.status_code == 500
    assert "删除落盘目录失败" in response.json()["detail"]
    assert repository.get(created["contract_id"]) is not None
    assert directory.is_dir()
