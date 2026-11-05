import json
import sqlite3
import threading
import time

import pytest
from fastapi.testclient import TestClient

from app_factory import make_app
from app.review_tasks import ReviewTaskManager, ReviewTaskStore
from infra.docx_parser import DocxParser
from infra.llm_client import FakeLLM
from infra.txt_parser import TxtParser

CONTRACT = (
    "劳动合同\n"
    "第一条 试用期三个月。\n"
    "第二条 月工资为人民币8000元。\n"
)

FAKE_RESPONSE = {
    "risks": [
        {
            "risk_type": "probation_exceed",
            "severity": "high",
            "excerpt": "试用期三个月",
            "explanation": "测试用风险",
            "is_violation": True,
        }
    ],
    "other_observations": [],
}


def _app(store_path, **kwargs):
    return make_app(
        client=FakeLLM(FAKE_RESPONSE),
        parsers={"txt": TxtParser(), "docx": DocxParser()},
        rate_limit=100,
        store_path=str(store_path),
        **kwargs,
    )


def _upload(
    client,
    name="contract.txt",
    content=CONTRACT.encode(),
    content_type="text/plain",
):
    return client.post(
        "/contracts", files={"file": (name, content, content_type)}
    )


def _wait(client, task_id, timeout=5):
    deadline = time.time() + timeout
    while time.time() < deadline:
        body = client.get(f"/result/{task_id}").json()
        if body["status"] in ("done", "failed"):
            return body
        time.sleep(0.01)
    raise AssertionError(f"task {task_id} did not finish")


def test_done_result_survives_restart(tmp_path):
    database = tmp_path / "tasks.sqlite"

    app = _app(database)
    with TestClient(app) as client:
        response = _upload(client)
        task_id = response.json()["contract_id"]
        before = _wait(client, task_id)["result"]

    app = _app(database)
    with TestClient(app) as client:
        after = client.get(f"/result/{task_id}").json()

    assert after["status"] == "done"
    assert json.dumps(
        after["result"], ensure_ascii=False, indent=2
    ) == json.dumps(before, ensure_ascii=False, indent=2)


def test_queued_task_recovers_and_completes(tmp_path):
    database = tmp_path / "tasks.sqlite"
    store = ReviewTaskStore(database)
    store.create_task(
        "queued-1", "queued.txt", CONTRACT, time.time()
    )
    store.close()

    app = _app(database)
    with TestClient(app) as client:
        body = _wait(client, "queued-1")

    assert body["status"] == "done"
    assert body["result"]["meta"]["run_id"] == "queued-1"


def test_expired_done_returns_410_and_is_deleted(tmp_path):
    database = tmp_path / "tasks.sqlite"
    app = _app(
        database, ttl_seconds=0.01, cleanup_interval=3600
    )
    with TestClient(app) as client:
        response = _upload(client)
        task_id = response.json()["contract_id"]
        assert _wait(client, task_id)["status"] == "done"

        time.sleep(0.05)
        manager = app.state.review_manager
        assert manager.cleanup_expired() == 1

        expired = client.get(f"/result/{task_id}")
        assert expired.status_code == 410
        assert expired.json()["detail"] == "合同审查已过期"

        assert manager.get(task_id) is None
        assert manager.store.get(task_id) is None
        assert manager.store.is_expired(task_id) is True


def test_expired_failed_cleaned_but_live_tasks_kept(tmp_path):
    database = tmp_path / "tasks.sqlite"
    app = _app(
        database, ttl_seconds=0.01, cleanup_interval=3600
    )
    with TestClient(app) as client:
        response = _upload(
            client,
            name="broken.docx",
            content=b"PK\x03\x04not a real docx",
            content_type=(
                "application/vnd.openxmlformats-officedocument."
                "wordprocessingml.document"
            ),
        )
        failed_id = response.json()["contract_id"]
        assert _wait(client, failed_id)["status"] == "failed"

        manager = app.state.review_manager
        old = time.time() - 100
        manager.store.create_task(
            "queued-keep", "q.txt", CONTRACT, old
        )
        manager.store.create_task(
            "processing-keep", "p.txt", CONTRACT, old
        )
        manager.store.mark_processing("processing-keep", old)

        time.sleep(0.05)
        assert manager.cleanup_expired() == 1

        assert client.get(f"/result/{failed_id}").status_code == 410
        assert manager.store.get(failed_id) is None
        assert manager.store.is_expired(failed_id) is True

        queued = manager.store.get("queued-keep")
        processing = manager.store.get("processing-keep")
        assert queued is not None and queued["status"] == "queued"
        assert (
            processing is not None
            and processing["status"] == "processing"
        )


def test_processing_task_recovers_to_terminal_state(tmp_path):
    database = tmp_path / "tasks.sqlite"
    store = ReviewTaskStore(database)
    store.create_task(
        "processing-1",
        "processing.txt",
        CONTRACT,
        time.time(),
    )
    store.mark_processing("processing-1", time.time())
    store.close()

    app = _app(database)
    with TestClient(app) as client:
        body = _wait(client, "processing-1")

    assert body["status"] in ("done", "failed")
    assert body["status"] not in ("queued", "processing")


@pytest.fixture()
def manager(tmp_path):
    review_manager = ReviewTaskManager(
        client=FakeLLM({"risks": [], "other_observations": []}),
        workers=1,
        max_inflight=1,
        queue_limit=2,
        store_path=str(tmp_path / "submit.sqlite"),
        ttl_seconds=86400,
        cleanup_interval=3600,
    )
    yield review_manager
    review_manager.close()


def test_submit_releases_admission_when_store_fails(
    manager, monkeypatch
):
    def boom(*args, **kwargs):
        raise sqlite3.OperationalError("db locked")

    monkeypatch.setattr(manager.store, "create_task", boom)
    before = manager._admission._value

    assert manager.submit("a.pdf", "x") is None
    assert manager._admission._value == before
    assert len(manager._tasks) == 0


def test_submit_survives_repeated_store_failures(
    manager, monkeypatch
):
    def boom(*args, **kwargs):
        raise sqlite3.OperationalError("db locked")

    monkeypatch.setattr(manager.store, "create_task", boom)
    capacity = manager._admission._value
    for _ in range(capacity):
        assert manager.submit("a.pdf", "x") is None

    monkeypatch.undo()
    task_id = manager.submit("b.pdf", "x")

    assert task_id is not None


def test_submit_cleans_up_when_executor_fails(
    manager, monkeypatch
):
    def boom(*args, **kwargs):
        raise RuntimeError("cannot schedule new futures")

    monkeypatch.setattr(manager._executor, "submit", boom)
    before = manager._admission._value

    assert manager.submit("a.txt", "x") is None
    assert manager._admission._value == before
    assert len(manager._tasks) == 0
    assert manager.store.list_all() == []


def test_cancel_queued_releases_admission(manager):
    before = manager._admission._value
    assert manager.reserve("c-queued") is True
    assert manager._admission._value == before - 1

    assert manager.cancel("c-queued") is True
    assert manager._admission._value == before
    assert manager.get("c-queued") is None
    assert manager.store.get("c-queued") is None


def test_cancel_processing_releases_exactly_once(tmp_path):
    started = threading.Event()
    release = threading.Event()

    class BlockingLLM:
        def call_llm(self, prompt):
            started.set()
            release.wait(timeout=5)
            return {"risks": [], "other_observations": []}

    review_manager = ReviewTaskManager(
        client=BlockingLLM(),
        workers=1,
        max_inflight=1,
        queue_limit=2,
        store_path=str(tmp_path / "cancel.sqlite"),
        ttl_seconds=86400,
        cleanup_interval=3600,
    )
    try:
        capacity = review_manager._admission._value
        assert review_manager.submit("c-running", CONTRACT) == "c-running"
        assert started.wait(timeout=5)

        assert review_manager.cancel("c-running") is True
        release.set()

        deadline = time.time() + 5
        while review_manager._admission._value != capacity:
            assert time.time() < deadline
            time.sleep(0.01)
        assert review_manager.get("c-running") is None
    finally:
        review_manager.close()
