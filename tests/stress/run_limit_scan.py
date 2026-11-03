"""M14 real concurrency limit scan.

Uses 32 tasks (each of the 16 corpus contracts duplicated once) so that
max_inflight=24/32 can actually be reached. workers == max_inflight,
rps=64. No pipeline code is changed.

Usage:
    python tests/stress/run_limit_scan.py
"""

import json
import os
import platform
import statistics
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

import psutil

ROOT = Path(__file__).resolve().parents[2]
CORPUS = ROOT / "tests" / "corpus_gen" / "generated"
EXPECTED = ROOT / "tests" / "corpus_gen" / "expected.json"
RESULTS = ROOT / "tests" / "stress" / "limit_scan.json"
INFLIGHT = [8, 16, 24, 32]
RPS = 64.0


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
    ordered = sorted(values)
    index = min(len(ordered) - 1, int(fraction * (len(ordered) - 1)))
    return ordered[index]


def _build_task_corpus(directory: Path) -> None:
    for source in sorted(CORPUS.glob("G-*.txt")):
        text = source.read_text(encoding="utf-8")
        for copy in (1, 2):
            (directory / f"{source.stem}__{copy}.txt").write_text(
                text, encoding="utf-8"
            )


def _base_name(name: str) -> str:
    return name.split("__")[0] + ".txt"


def _count_events(events):
    counts = {
        "retries": 0,
        "timeout": 0,
        "empty_response": 0,
        "429": 0,
        "invalid_json": 0,
        "connection": 0,
        "llm_failed": 0,
    }
    for event in events:
        name = event.get("event")
        if name == "llm.retry":
            counts["retries"] += 1
        elif name == "llm.failed":
            counts["llm_failed"] += 1
        elif name == "llm.call_failed":
            reason = event.get("reason")
            if reason in counts:
                counts[reason] += 1
    return counts


def _run_row(directory: Path, max_inflight: int) -> dict:
    command = [
        sys.executable,
        "-m",
        "src.cli",
        "batch",
        str(directory),
        "--workers",
        str(max_inflight),
        "--max-inflight",
        str(max_inflight),
        "--rps",
        str(RPS),
    ]
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
    wall_elapsed = round(time.monotonic() - started, 3)
    samples = sampler.finish()
    if process.returncode != 0:
        raise SystemExit(
            f"inflight={max_inflight} failed:\n{process.stderr[-2000:]}"
        )

    summary = json.loads(process.stdout)
    log_path = ROOT / "logs" / f"review_{summary['run_id']}.log"
    events = [
        json.loads(line)
        for line in log_path.read_text(encoding="utf-8").splitlines()
    ]
    counts = _count_events(events)
    latencies = [item["elapsed"] for item in summary["items"]]
    gt = json.loads(EXPECTED.read_text(encoding="utf-8"))
    gt_match = sum(
        1
        for item in summary["items"]
        if item["risk_types"]
        == gt[_base_name(item["file"])]["expected_risks"]
    )
    total = summary["total_elapsed"]

    return {
        "max_inflight": max_inflight,
        "workers": max_inflight,
        "tasks": len(summary["items"]),
        "run_id": summary["run_id"],
        "total_elapsed": total,
        "wall_elapsed": wall_elapsed,
        "throughput": round(len(summary["items"]) / total, 2),
        "p50": round(statistics.median(latencies), 3),
        "p95": round(_percentile(latencies, 0.95), 3),
        "max": round(max(latencies), 3),
        "ok": summary["ok"],
        "llm_failed": summary["llm_failed"],
        "failed": summary["failed"],
        "gt_match": gt_match,
        "cpu_avg": round(statistics.mean(samples), 1) if samples else 0,
        "cpu_max": round(max(samples), 1) if samples else 0,
        "tokens": summary["tokens"],
        **counts,
    }


def main():
    rows = []
    with tempfile.TemporaryDirectory(prefix="m14_scan_") as temp:
        task_corpus = Path(temp)
        _build_task_corpus(task_corpus)
        for max_inflight in INFLIGHT:
            row = _run_row(task_corpus, max_inflight)
            rows.append(row)
            RESULTS.write_text(
                json.dumps(
                    {
                        "environment": {
                            "platform": platform.platform(),
                            "processor": platform.processor(),
                            "cpu_count": os.cpu_count(),
                            "python": sys.version.split()[0],
                            "rps": RPS,
                            "tasks": 32,
                        },
                        "rows": rows,
                    },
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
            print(
                f"inflight={max_inflight:>2} total={row['total_elapsed']:>6.2f}s "
                f"throughput={row['throughput']:>5.2f}/s "
                f"p50={row['p50']:>5.2f}s p95={row['p95']:>5.2f}s "
                f"max={row['max']:>5.2f}s "
                f"retries={row['retries']} timeout={row['timeout']} "
                f"empty={row['empty_response']} 429={row['429']} "
                f"llm_failed={row['llm_failed']} failed={row['failed']} "
                f"gt={row['gt_match']}/32 "
                f"cpu={row['cpu_avg']}/{row['cpu_max']}% "
                f"tokens={row['tokens']['total_tokens']}"
            )
    print(f"wrote {RESULTS}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
