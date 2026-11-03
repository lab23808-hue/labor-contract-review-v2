"""M13 stress ladder and chaos run.

Runs the real batch CLI against the 16 generated corpus contracts for
workers = 1/2/4/8/16/32, samples system CPU with psutil, and records the
per-item latency distribution, failures, token totals and GT match count.

Usage:
    python tests/stress/run_stress.py
"""

import json
import os
import platform
import statistics
import subprocess
import sys
import threading
import time
from pathlib import Path

import psutil

ROOT = Path(__file__).resolve().parents[2]
CORPUS = ROOT / "tests" / "corpus_gen" / "generated"
EXPECTED = ROOT / "tests" / "corpus_gen" / "expected.json"
RESULTS = ROOT / "tests" / "stress" / "results.json"
WORKERS = [1, 2, 4, 8, 16, 32]
RPS = 8.0
MAX_INFLIGHT = 8
CHAOS_RATE = 0.25


class CpuSampler:
    def __init__(self):
        self.samples = []
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def _run(self):
        while not self._stop.is_set():
            self.samples.append(psutil.cpu_percent(interval=0.5))

    def start(self):
        self._thread.start()

    def finish(self):
        self._stop.set()
        self._thread.join()
        return self.samples


def _percentile(values, fraction):
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, int(fraction * (len(ordered) - 1)))
    return ordered[index]


def _run_batch(workers, chaos=False):
    command = [
        sys.executable,
        "-m",
        "src.cli",
        "batch",
        str(CORPUS),
        "--workers",
        str(workers),
        "--rps",
        str(RPS),
        "--max-inflight",
        str(MAX_INFLIGHT),
        "--trace",
    ]
    if chaos:
        command += ["--chaos", "--chaos-rate", str(CHAOS_RATE)]

    sampler = CpuSampler()
    sampler.start()
    started = time.monotonic()
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    process = subprocess.run(
        command,
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
    )
    elapsed = round(time.monotonic() - started, 3)
    samples = sampler.finish()

    if process.returncode != 0:
        raise SystemExit(
            f"batch workers={workers} failed:\n{process.stderr[-2000:]}"
        )
    summary = json.loads(process.stdout)
    latencies = [item["elapsed"] for item in summary["items"]]
    gt = json.loads(EXPECTED.read_text(encoding="utf-8"))
    gt_match = sum(
        1
        for item in summary["items"]
        if item["risk_types"] == gt[item["file"]]["expected_risks"]
    )

    return {
        "workers": workers,
        "chaos": chaos,
        "run_id": summary["run_id"],
        "total_elapsed": summary["total_elapsed"],
        "wall_elapsed": elapsed,
        "ok": summary["ok"],
        "llm_failed": summary["llm_failed"],
        "failed": summary["failed"],
        "gt_match": gt_match,
        "p50": round(statistics.median(latencies), 3) if latencies else 0,
        "p95": round(_percentile(latencies, 0.95), 3),
        "max": round(max(latencies), 3) if latencies else 0,
        "cpu_avg": round(statistics.mean(samples), 1) if samples else 0,
        "cpu_max": round(max(samples), 1) if samples else 0,
        "tokens": summary["tokens"],
    }


def main():
    ladder = []
    for workers in WORKERS:
        result = _run_batch(workers)
        ladder.append(result)
        print(
            f"N={workers:>2} total={result['total_elapsed']:>6.2f}s "
            f"p50={result['p50']:>5.2f}s p95={result['p95']:>5.2f}s "
            f"max={result['max']:>5.2f}s "
            f"ok={result['ok']} llm_failed={result['llm_failed']} "
            f"failed={result['failed']} gt={result['gt_match']}/16 "
            f"cpu={result['cpu_avg']}/{result['cpu_max']}% "
            f"tokens={result['tokens']['total_tokens']}"
        )

    chaos = _run_batch(workers=4, chaos=True)
    print(
        f"CHAOS ok={chaos['ok']} llm_failed={chaos['llm_failed']} "
        f"failed={chaos['failed']} gt={chaos['gt_match']}/16 "
        f"total={chaos['total_elapsed']}s"
    )

    payload = {
        "environment": {
            "platform": platform.platform(),
            "processor": platform.processor(),
            "cpu_count": os.cpu_count(),
            "python": sys.version.split()[0],
            "rps": RPS,
            "max_inflight": MAX_INFLIGHT,
            "corpus_size": len(list(CORPUS.glob("*.txt"))),
        },
        "ladder": ladder,
        "chaos": chaos,
    }
    RESULTS.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"wrote {RESULTS}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
