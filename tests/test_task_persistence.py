import json
import time

from fastapi.testclient import TestClient

from api.review_app import create_review_app
from app.review_tasks import ReviewTaskStore
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
    return create_review_app(
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
        "/upload", files={"file": (name, content, content_type)}
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
        task_id = response.json()["task_id"]
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
        "queued-1", "queued.txt", CONTRACT.encode(), time.time()
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
        task_id = response.json()["task_id"]
        assert _wait(client, task_id)["status"] == "done"

        time.sleep(0.05)
        manager = app.state.review_manager
        assert manager.cleanup_expired() == 1

        expired = client.get(f"/result/{task_id}")
        assert expired.status_code == 410
        assert expired.json()["detail"] == "任务已过期"

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
            content=b"not a real docx",
            content_type=(
                "application/vnd.openxmlformats-officedocument."
                "wordprocessingml.document"
            ),
        )
        failed_id = response.json()["task_id"]
        assert _wait(client, failed_id)["status"] == "failed"

        manager = app.state.review_manager
        old = time.time() - 100
        manager.store.create_task(
            "queued-keep", "q.txt", CONTRACT.encode(), old
        )
        manager.store.create_task(
            "processing-keep", "p.txt", CONTRACT.encode(), old
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
        CONTRACT.encode(),
        time.time(),
    )
    store.mark_processing("processing-1", time.time())
    store.close()

    app = _app(database)
    with TestClient(app) as client:
        body = _wait(client, "processing-1")

    assert body["status"] in ("done", "failed")
    assert body["status"] not in ("queued", "processing")
