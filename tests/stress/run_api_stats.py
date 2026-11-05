"""Real /stats concurrency check for the review upload API.

Uses the configured real DeepSeek client, uploads 8 corpus contracts with
workers/inflight = 4, prints peak and final /stats, and the per-file risk
types. Not a pytest test.

Usage:
    python tests/stress/run_api_stats.py
"""

import json
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from fastapi.testclient import TestClient

from api.app import create_app
from infra.sqlite_repository import SQLiteContractRepository

CORPUS = Path(__file__).resolve().parents[1] / "corpus_gen" / "generated"
FILES = [f"G-{index:02d}.txt" for index in range(1, 9)]


def main():
    app = create_app(
        repository=SQLiteContractRepository(
            tempfile.mkdtemp(prefix="api-stats-")
        ),
        review_workers=4,
        review_max_inflight=4,
        review_rps=8,
        review_queue_limit=50,
        review_rate_limit=100,
        review_store_path=":memory:",
    )
    with TestClient(app) as client:
        def upload(name):
            content = (CORPUS / name).read_bytes()
            return client.post(
                "/contracts",
                files={"file": (name, content, "text/plain")},
            )

        with ThreadPoolExecutor(max_workers=8) as pool:
            responses = list(pool.map(upload, FILES))

        task_ids = []
        for response in responses:
            assert response.status_code == 201, response.text
            task_ids.append(response.json()["contract_id"])

        print("uploads:", [response.status_code for response in responses])
        print(
            "peak stats:",
            json.dumps(
                client.get("/stats").json(), ensure_ascii=False
            ),
        )

        results = {}
        deadline = time.time() + 60
        while time.time() < deadline:
            pending = False
            for name, task_id in zip(FILES, task_ids):
                body = client.get(f"/result/{task_id}").json()
                if body["status"] in ("queued", "processing"):
                    pending = True
                elif body["status"] == "done":
                    results[name] = [
                        risk["risk_type"]
                        for risk in body["result"]["risks"]
                    ]
                else:
                    results[name] = f"failed: {body['error']}"
            if not pending:
                break
            time.sleep(0.2)

        print("results:", json.dumps(results, ensure_ascii=False))
        print(
            "final stats:",
            json.dumps(
                client.get("/stats").json(), ensure_ascii=False
            ),
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
