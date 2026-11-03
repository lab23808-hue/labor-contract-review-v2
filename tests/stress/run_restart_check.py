"""Real submit -> done -> restart -> query check for M16 persistence.

Uses the configured real DeepSeek client and a temporary SQLite file.
Not a pytest test.

Usage:
    python tests/stress/run_restart_check.py
"""

import json
import sys
import tempfile
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

CORPUS = ROOT / "tests" / "corpus_gen" / "generated"
FILE = "G-04.txt"


def _wait(client, task_id, timeout=60):
    deadline = time.time() + timeout
    while time.time() < deadline:
        body = client.get(f"/result/{task_id}").json()
        if body["status"] in ("done", "failed"):
            return body
        time.sleep(0.2)
    raise SystemExit(f"task {task_id} did not finish")


def main():
    client = DeepSeekLLMClient(
        throttle=RequestThrottle(rate=8, max_inflight=8)
    )
    content = (CORPUS / FILE).read_bytes()

    with tempfile.TemporaryDirectory(prefix="m16_restart_") as temp:
        database = Path(temp) / "tasks.sqlite"

        app = create_review_app(
            client=client,
            store_path=str(database),
            rate_limit=100,
        )
        with TestClient(app) as http:
            response = http.post(
                "/upload",
                files={"file": (FILE, content, "text/plain")},
            )
            task_id = response.json()["task_id"]
            before = _wait(http, task_id)
            print("before restart:", json.dumps(
                {
                    "task_id": task_id,
                    "status": before["status"],
                    "risk_types": [
                        risk["risk_type"]
                        for risk in before["result"]["risks"]
                    ],
                    "tokens": before["result"]["meta"]["tokens"],
                },
                ensure_ascii=False,
            ))

        app = create_review_app(
            client=client,
            store_path=str(database),
            rate_limit=100,
        )
        with TestClient(app) as http:
            after = http.get(f"/result/{task_id}").json()
            print("after restart: ", json.dumps(
                {
                    "task_id": after["task_id"],
                    "status": after["status"],
                    "risk_types": [
                        risk["risk_type"]
                        for risk in after["result"]["risks"]
                    ],
                    "tokens": after["result"]["meta"]["tokens"],
                },
                ensure_ascii=False,
            ))

        assert json.dumps(
            before["result"], ensure_ascii=False, indent=2
        ) == json.dumps(
            after["result"], ensure_ascii=False, indent=2
        )
        print("match: True")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
