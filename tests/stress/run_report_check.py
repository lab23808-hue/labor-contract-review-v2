"""Render real HTML report pages for a multi-risk and a clean contract.

Uses the configured real DeepSeek client and an in-memory task store.
Not a pytest test.

Usage:
    python tests/stress/run_report_check.py
"""

import html
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from fastapi.testclient import TestClient

from api.review_app import create_review_app
from infra.llm_client import DeepSeekLLMClient
from infra.rate_limiter import RequestThrottle

CASES = [
    ("C-004（多风险）", ROOT / "tests/fixtures/realistic/C-004.txt"),
    ("G-01（无风险）", ROOT / "tests/corpus_gen/generated/G-01.txt"),
]


def _wait(client, task_id, timeout=120):
    deadline = time.time() + timeout
    while time.time() < deadline:
        body = client.get(f"/result/{task_id}").json()
        if body["status"] in ("done", "failed"):
            return body
        time.sleep(0.2)
    raise SystemExit(f"task {task_id} did not finish")


def _visible_text(page: str) -> str:
    page = re.sub(r"<style.*?</style>", " ", page, flags=re.S)
    page = re.sub(r"<[^>]+>", " ", page)
    page = html.unescape(page)
    return re.sub(r"\s+", " ", page).strip()


def main():
    client = DeepSeekLLMClient(
        throttle=RequestThrottle(rate=8, max_inflight=4)
    )
    app = create_review_app(
        client=client,
        store_path=":memory:",
        rate_limit=100,
    )
    with TestClient(app) as http:
        for label, path in CASES:
            content = path.read_bytes()
            response = http.post(
                "/upload",
                files={"file": (path.name, content, "text/plain")},
            )
            task_id = response.json()["task_id"]
            body = _wait(http, task_id)
            page = http.get(f"/report/{task_id}")
            print(f"\n=== {label} / {task_id} / {body['status']} ===")
            print(_visible_text(page.text)[:1800])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
