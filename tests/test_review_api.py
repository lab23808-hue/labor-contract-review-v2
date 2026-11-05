import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from fastapi.testclient import TestClient

from app_factory import make_app
from app.review_engine import run_full_review
from domain.cleaning import clean_text
from domain.extractor import extract_fields
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


class BlockingLLM:
    def __init__(self):
        self.started = threading.Event()
        self.release = threading.Event()

    def call_llm(self, prompt):
        self.started.set()
        self.release.wait(timeout=5)
        return {"risks": [], "other_observations": []}


def _app(**kwargs):
    kwargs.setdefault("store_path", ":memory:")
    return make_app(
        client=FakeLLM(FAKE_RESPONSE),
        parsers={"txt": TxtParser(), "docx": DocxParser()},
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


def test_concurrent_same_ip_rate_limited_to_five():
    app = _app(rate_limit=5, queue_limit=50, workers=4)
    with TestClient(app) as client:
        barrier = threading.Barrier(10)

        def post(index):
            barrier.wait()
            return _upload(
                client,
                content=(CONTRACT + f"\n第{index}份").encode(),
            )

        with ThreadPoolExecutor(max_workers=10) as pool:
            responses = list(pool.map(post, range(10)))

    codes = [response.status_code for response in responses]
    assert codes.count(201) == 5
    assert codes.count(429) == 5
    for response in responses:
        if response.status_code == 429:
            assert response.headers["X-RateLimit-Remaining"] == "0"
            assert "Retry-After" in response.headers


def test_queue_full_returns_503_and_does_not_enqueue():
    blocking = BlockingLLM()
    app = make_app(
        client=blocking,
        parsers={"txt": TxtParser()},
        workers=1,
        queue_limit=1,
        rate_limit=100,
        store_path=":memory:",
    )
    with TestClient(app) as client:
        first = _upload(client)
        assert first.status_code == 201
        task_id = first.json()["contract_id"]
        assert blocking.started.wait(timeout=5)

        second = _upload(client)

        assert second.status_code == 503
        assert second.json()["detail"] == "系统忙"
        stats = client.get("/stats").json()
        assert stats["total"] == 1
        assert stats["in_flight"] == 1
        assert stats["queue_depth"] == 0

        blocking.release.set()
        assert _wait(client, task_id)["status"] == "done"
        stats = client.get("/stats").json()
        assert stats["processed"] == 1
        assert stats["queue_depth"] == 0


def test_rejects_invalid_extension_and_oversize():
    app = _app()
    with TestClient(app) as client:
        invalid = _upload(client, name="malware.exe", content=b"x")
        assert invalid.status_code == 422
        assert ".exe" in invalid.json()["detail"]

        oversized = _upload(
            client,
            name="big.txt",
            content=b"x" * (2 * 1024 * 1024 + 1),
        )
        assert oversized.status_code == 422
        assert "上限" in oversized.json()["detail"]


def test_upload_result_matches_single_run():
    app = _app()
    with TestClient(app) as client:
        response = _upload(client)
        assert response.status_code == 201
        task_id = response.json()["contract_id"]
        body = _wait(client, task_id)
        assert body["status"] == "done"

        text = clean_text(
            TxtParser().parse("contract.txt", CONTRACT.encode())
        )
        single = run_full_review(
            text,
            extract_fields(text),
            FakeLLM(FAKE_RESPONSE),
            run_id=task_id,
        )

        assert json.dumps(
            body["result"], ensure_ascii=False, indent=2
        ) == json.dumps(single, ensure_ascii=False, indent=2)


def test_failed_item_does_not_stop_other_tasks():
    app = make_app(
        client=FakeLLM(FAKE_RESPONSE),
        parsers={"txt": TxtParser(), "docx": DocxParser()},
        workers=2,
        queue_limit=50,
        rate_limit=100,
        store_path=":memory:",
    )
    with TestClient(app) as client:
        good = _upload(client)
        bad = _upload(
            client,
            name="broken.docx",
            content=b"PK\x03\x04not a real docx",
            content_type=(
                "application/vnd.openxmlformats-officedocument."
                "wordprocessingml.document"
            ),
        )

        good_body = _wait(client, good.json()["contract_id"])
        bad_body = _wait(client, bad.json()["contract_id"])

        assert good_body["status"] == "done"
        assert bad_body["status"] == "failed"
        assert "docx" in bad_body["error"].lower()

        third = _upload(
            client,
            content=(CONTRACT + "\n第三份合同").encode(),
        )
        assert _wait(client, third.json()["contract_id"])["status"] == "done"

        stats = client.get("/stats").json()
        assert stats["processed"] == 2
        assert stats["failed"] == 1
