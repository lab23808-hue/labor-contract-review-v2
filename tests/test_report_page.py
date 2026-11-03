import threading
import time

from fastapi.testclient import TestClient

from api.review_app import create_review_app
from infra.docx_parser import DocxParser
from infra.llm_client import FakeLLM
from infra.txt_parser import TxtParser

REPORT_CONTRACT = (
    "劳动合同\n"
    "第一条 合同期限\n"
    "本合同为固定期限劳动合同，自2025年3月1日起至2027年2月28日止，"
    "其中试用期为二个月（自2025年3月1日起至2025年4月30日止）。\n"
    "第二条 劳动报酬\n"
    "乙方月工资为人民币10000元，试用期期间月工资为7000元。\n"
    "第三条 社会保险\n"
    "甲方依法为乙方办理社会保险登记，缴纳养老、医疗、失业、工伤、"
    "生育保险费。\n"
    "第四条 续签\n"
    "本合同期满后如续签，仍为固定期限劳动合同，"
    "乙方不得要求签订无固定期限劳动合同。\n"
)

CLEAN_CONTRACT = (
    "劳动合同\n"
    "第一条 合同期限\n"
    "本合同为固定期限劳动合同，自2025年3月1日起至2027年2月28日止，"
    "其中试用期为二个月（自2025年3月1日起至2025年4月30日止）。\n"
    "第二条 劳动报酬\n"
    "乙方月工资为人民币10000元，试用期期间月工资为10000元。\n"
    "第三条 社会保险\n"
    "甲方依法为乙方办理社会保险登记，缴纳养老、医疗、失业、工伤、"
    "生育保险费。\n"
)

REPORT_RESPONSE = {
    "risks": [
        {
            "risk_type": "wage_below_floor",
            "severity": "medium",
            "excerpt": "试用期期间月工资为7000元",
            "explanation": "LLM 版",
            "is_violation": True,
        },
        {
            "risk_type": "open_ended_denied",
            "severity": "medium",
            "excerpt": (
                "本合同期满后如续签，仍为固定期限劳动合同，"
                "乙方不得要求签订无固定期限劳动合同"
            ),
            "explanation": "排除无固定期限合同权利",
            "is_violation": True,
        },
    ],
    "other_observations": [],
}

EMPTY_RESPONSE = {"risks": [], "other_observations": []}


class BlockingLLM:
    def __init__(self):
        self.started = threading.Event()
        self.release = threading.Event()

    def call_llm(self, prompt):
        self.started.set()
        self.release.wait(timeout=5)
        return {"risks": [], "other_observations": []}


def _app(response=None, **kwargs):
    if response is None:
        response = REPORT_RESPONSE
    kwargs.setdefault("store_path", ":memory:")
    return create_review_app(
        client=FakeLLM(response),
        parsers={"txt": TxtParser(), "docx": DocxParser()},
        rate_limit=100,
        **kwargs,
    )


def _upload(
    client,
    name="contract.txt",
    content=REPORT_CONTRACT.encode(),
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


def test_done_report_contains_risk_details():
    app = _app()
    with TestClient(app) as client:
        task_id = _upload(client).json()["task_id"]
        assert _wait(client, task_id)["status"] == "done"
        page = client.get(f"/report/{task_id}")

    assert page.status_code == 200
    html = page.text
    for expected in (
        "试用期工资低于下限",
        "无固定期限权利被排除",
        "试用期期间月工资为7000元",
        "规则命中",
        "LLM 判定",
        "7000/10000=70%",
        "风险条数",
        "自动审查结果仅供参考",
    ):
        assert expected in html


def test_no_risk_report_shows_checks():
    app = _app(response=EMPTY_RESPONSE)
    with TestClient(app) as client:
        task_id = _upload(
            client, content=CLEAN_CONTRACT.encode()
        ).json()["task_id"]
        assert _wait(client, task_id)["status"] == "done"
        page = client.get(f"/report/{task_id}")

    assert page.status_code == 200
    html = page.text
    assert "未检出风险条款" in html
    assert "试用期期限是否超过法定上限" in html
    assert "是否缺失社会保险条款" in html


def test_status_and_failed_pages():
    blocking = BlockingLLM()
    app = create_review_app(
        client=blocking,
        parsers={"txt": TxtParser(), "docx": DocxParser()},
        workers=1,
        queue_limit=50,
        rate_limit=100,
        store_path=":memory:",
    )
    with TestClient(app) as client:
        first = _upload(client).json()["task_id"]
        assert blocking.started.wait(timeout=5)
        second = _upload(client).json()["task_id"]

        processing = client.get(f"/report/{first}")
        queued = client.get(f"/report/{second}")

        assert processing.status_code == 200
        assert "审查中" in processing.text
        assert 'http-equiv="refresh"' in processing.text
        assert "30" in processing.text

        assert queued.status_code == 200
        assert "排队中" in queued.text
        assert 'http-equiv="refresh"' in queued.text

        blocking.release.set()
        assert _wait(client, first)["status"] == "done"
        assert _wait(client, second)["status"] == "done"

        failed_id = _upload(
            client,
            name="broken.docx",
            content=b"not a real docx",
            content_type=(
                "application/vnd.openxmlformats-officedocument."
                "wordprocessingml.document"
            ),
        ).json()["task_id"]
        assert _wait(client, failed_id)["status"] == "failed"

        failed = client.get(f"/report/{failed_id}")
        assert failed.status_code == 200
        assert "审查失败" in failed.text
        assert "docx" in failed.text.lower()
        assert 'http-equiv="refresh"' not in failed.text


def test_expired_and_missing_report_pages():
    app = _app(ttl_seconds=0.01, cleanup_interval=3600)
    with TestClient(app) as client:
        task_id = _upload(client).json()["task_id"]
        assert _wait(client, task_id)["status"] == "done"

        time.sleep(0.05)
        manager = app.state.review_manager
        assert manager.cleanup_expired() == 1

        expired = client.get(f"/report/{task_id}")
        assert expired.status_code == 410
        assert "任务已过期" in expired.text

        missing = client.get("/report/does-not-exist")
        assert missing.status_code == 404
        assert "任务不存在" in missing.text

        assert client.get(f"/result/{task_id}").status_code == 410
        assert client.get("/result/does-not-exist").status_code == 404


def test_report_escapes_untrusted_html():
    xss_response = {
        "risks": [
            {
                "risk_type": "probation_exceed",
                "severity": "high",
                "excerpt": "试用期三个月",
                "explanation": "<script>alert(2)</script>",
                "is_violation": True,
            }
        ],
        "other_observations": [],
    }
    contract = (
        "劳动合同\n"
        "第一条 试用期三个月。\n"
        "第二条 社会保险\n"
        "甲方依法为乙方办理社会保险登记，缴纳养老、医疗、失业、"
        "工伤、生育保险费。\n"
    )
    app = _app(response=xss_response)
    with TestClient(app) as client:
        task_id = _upload(
            client,
            name="<script>alert(1)</script>.txt",
            content=contract.encode(),
        ).json()["task_id"]
        assert _wait(client, task_id)["status"] == "done"
        page = client.get(f"/report/{task_id}")

    html = page.text
    assert page.status_code == 200
    assert "<script>alert(2)</script>" not in html
    assert "&lt;script&gt;alert(2)&lt;/script&gt;" in html
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;.txt" in html
