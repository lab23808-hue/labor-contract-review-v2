import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Callable, Mapping, Optional

from app.review import run_full_review
from domain.cleaning import clean_text
from domain.extractor import extract_fields
from domain.ports import DocumentParser, TraceSink

logger = logging.getLogger(__name__)


def list_contracts(
    directory: Path, parsers: Mapping[str, DocumentParser]
) -> list[Path]:
    suffixes = {f".{name.lower()}" for name in parsers}
    return sorted(
        path
        for path in Path(directory).iterdir()
        if path.is_file() and path.suffix.lower() in suffixes
    )


def read_parsed(
    path: Path, parsers: Mapping[str, DocumentParser]
) -> str:
    suffix = path.suffix.lower().lstrip(".")
    parser = parsers.get(suffix)
    if parser is None:
        raise ValueError(f"不支持的文件类型：{path.suffix}")
    return parser.parse(path.name, path.read_bytes())


def run_batch(
    directory: Path,
    client: Any,
    workers: int,
    batch_run_id: str,
    parsers: Mapping[str, DocumentParser],
    trace_root: Optional[Path] = None,
    trace_factory: Optional[Callable[[Path], TraceSink]] = None,
) -> dict[str, Any]:
    files = list_contracts(directory, parsers)
    batch_started = time.monotonic()
    results: list[dict[str, Any]] = []

    def process(index: int, path: Path) -> dict[str, Any]:
        item_run_id = f"{batch_run_id}_{index:02d}"
        trace_dir = None
        trace = None
        if trace_root is not None and trace_factory is not None:
            trace_dir = Path(trace_root) / f"{index:02d}_{path.stem}"
            trace = trace_factory(trace_dir)
        started = time.monotonic()
        try:
            parsed = read_parsed(path, parsers)
            cleaned = clean_text(parsed)
            report = run_full_review(
                cleaned,
                extract_fields(cleaned),
                llm_client=client,
                trace=trace,
                run_id=item_run_id,
            )
            elapsed = round(time.monotonic() - started, 3)
            status = "llm_failed" if report["meta"]["llm_failed"] else "ok"
            result = {
                "file": path.name,
                "run_id": item_run_id,
                "status": status,
                "elapsed": elapsed,
                "trace_dir": str(trace_dir) if trace_dir is not None else None,
                "risk_types": [
                    risk["risk_type"] for risk in report["risks"]
                ],
                "tokens": report["meta"]["tokens"],
                "error": report.get("error"),
                "report": report,
            }
            if status == "ok":
                logger.info(
                    "",
                    extra={
                        "event": "batch.item_done",
                        "file": path.name,
                        "item_run_id": item_run_id,
                        "elapsed": elapsed,
                        "risk_count": report["meta"]["risk_count"],
                    },
                )
            else:
                logger.warning(
                    "",
                    extra={
                        "event": "batch.item_failed",
                        "file": path.name,
                        "item_run_id": item_run_id,
                        "status": status,
                        "elapsed": elapsed,
                        "error": report.get("error"),
                    },
                )
            return result
        except Exception as exc:
            elapsed = round(time.monotonic() - started, 3)
            error = f"{type(exc).__name__}: {exc}"
            logger.warning(
                "",
                extra={
                    "event": "batch.item_failed",
                    "file": path.name,
                    "item_run_id": item_run_id,
                    "status": "error",
                    "elapsed": elapsed,
                    "error": error,
                },
            )
            return {
                "file": path.name,
                "run_id": item_run_id,
                "status": "error",
                "elapsed": elapsed,
                "trace_dir": str(trace_dir) if trace_dir is not None else None,
                "risk_types": [],
                "tokens": _zero_tokens(),
                "error": error,
                "report": None,
            }

    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = [
            pool.submit(process, index, path)
            for index, path in enumerate(files, start=1)
        ]
        for future in as_completed(futures):
            results.append(future.result())

    results.sort(key=lambda item: item["file"])
    total_elapsed = round(time.monotonic() - batch_started, 3)
    ok = sum(1 for item in results if item["status"] == "ok")
    llm_failed = sum(
        1 for item in results if item["status"] == "llm_failed"
    )
    failed = sum(1 for item in results if item["status"] == "error")
    tokens = _sum_tokens(results)

    summary = {
        "run_id": batch_run_id,
        "workers": workers,
        "directory": str(Path(directory).resolve()),
        "total_elapsed": total_elapsed,
        "ok": ok,
        "llm_failed": llm_failed,
        "failed": failed,
        "tokens": tokens,
        "items": results,
    }
    logger.info(
        "",
        extra={
            "event": "batch.summary",
            "run_id": batch_run_id,
            "workers": workers,
            "ok": ok,
            "llm_failed": llm_failed,
            "failed": failed,
            "total_elapsed": total_elapsed,
            "tokens": tokens,
        },
    )
    return summary


def _sum_tokens(results: list[dict[str, Any]]) -> dict[str, Any]:
    total = _zero_tokens()
    estimated = False
    for item in results:
        tokens = item.get("tokens") or {}
        total["prompt_tokens"] += tokens.get("prompt_tokens", 0)
        total["completion_tokens"] += tokens.get("completion_tokens", 0)
        total["total_tokens"] += tokens.get("total_tokens", 0)
        estimated = estimated or bool(tokens.get("estimated", False))
    total["estimated"] = estimated
    return total


def _zero_tokens() -> dict[str, Any]:
    return {
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
        "estimated": False,
    }
