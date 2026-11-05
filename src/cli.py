"""CLI: python -m src.cli review <file> | batch <dir>."""

import argparse
import json
import logging
import secrets
import sys
import time
from datetime import datetime
from pathlib import Path

_SRC = Path(__file__).resolve().parent
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from app.batch import run_batch
from app.review import run_full_review
from domain.cleaning import clean_text
from domain.extractor import extract_fields
from infra.docx_parser import DocxParser
from infra.llm_client import DeepSeekLLMClient
from infra.mineru_parser import MinerUParser
from infra.rate_limiter import RequestThrottle
from infra.trace import TraceRecorder
from infra.txt_parser import TxtParser
from logging_setup import setup_logging

logger = logging.getLogger(__name__)


def _read_parsed(path: Path) -> str:
    suffix = path.suffix.lower()
    content = path.read_bytes()
    if suffix == ".txt":
        return TxtParser().parse(path.name, content)
    if suffix == ".docx":
        return DocxParser().parse(path.name, content)
    if suffix == ".pdf":
        from infra.mineru_parser import MinerUParser

        return MinerUParser().parse(path.name, content)
    raise SystemExit(f"不支持的文件类型：{suffix or '(无扩展名)'}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m src.cli")
    subparsers = parser.add_subparsers(dest="command", required=True)

    review = subparsers.add_parser("review", help="对合同文件执行完整审查")
    review.add_argument("file")
    review.add_argument(
        "--trace",
        action="store_true",
        help="把各阶段中间产物写入 trace_<run_id>/",
    )

    batch = subparsers.add_parser("batch", help="对目录内合同批量审查")
    batch.add_argument("directory")
    batch.add_argument(
        "--workers",
        type=int,
        default=4,
        help="并发 worker 数；批量扫描时通常设为 max_inflight",
    )
    batch.add_argument("--trace", action="store_true")
    batch.add_argument("--chaos", action="store_true")
    batch.add_argument("--chaos-rate", type=float, default=0.2)
    batch.add_argument(
        "--rps",
        type=float,
        default=8.0,
        help="令牌桶每秒请求数，默认 8",
    )
    batch.add_argument(
        "--max-inflight",
        type=int,
        default=8,
        help=(
            "同时在途的 LLM 请求上限。默认 8（延迟友好）；"
            "批量吞吐可调到 32（M14：吞吐 +56%%，p50 约 3.6 倍）"
        ),
    )

    args = parser.parse_args(argv)
    if args.command == "review":
        return _run_review(args)
    if args.command == "batch":
        return _run_batch(args)
    return 1


def _run_review(args) -> int:
    path = Path(args.file)
    if not path.is_file():
        raise SystemExit(f"文件不存在：{path}")

    run_id = _new_run_id()
    setup_logging(run_id)
    started = time.monotonic()
    logger.info(
        "",
        extra={
            "event": "pipeline.run_start",
            "run_id": run_id,
            "path": str(path.resolve()),
            "size": path.stat().st_size,
            "suffix": path.suffix,
        },
    )

    parsed_text = _read_parsed(path)
    cleaned_text = clean_text(parsed_text)
    removed = len(parsed_text) - len(cleaned_text)
    logger.info(
        "",
        extra={"event": "pipeline.parse_done", "chars": len(parsed_text)},
    )
    logger.info(
        "",
        extra={
            "event": "pipeline.clean_done",
            "chars": len(cleaned_text),
            "removed": removed,
        },
    )

    trace = None
    if args.trace:
        trace_dir = _create_trace_dir(run_id)
        trace = TraceRecorder(trace_dir)
        meta = {
            "path": str(path.resolve()),
            "size": path.stat().st_size,
            "suffix": path.suffix,
        }
        trace.write_json("00_input.meta.json", meta)
        trace.write_text(
            "01_parsed.txt",
            _with_footer(parsed_text, f"字符数: {len(parsed_text)}"),
        )
        trace.write_text(
            "02_cleaned.txt",
            _with_footer(
                cleaned_text,
                f"字符数: {len(cleaned_text)}",
                f"删除字符数: {removed}",
            ),
        )
        trace.log(
            f"input path={meta['path']} size={meta['size']} "
            f"suffix={meta['suffix'] or '(none)'}"
        )
        trace.log(f"parsed chars={len(parsed_text)}")
        trace.log(f"cleaned chars={len(cleaned_text)} removed={removed}")

    report = run_full_review(
        cleaned_text,
        extract_fields(cleaned_text),
        DeepSeekLLMClient(),
        trace=trace,
        run_id=run_id,
    )
    elapsed = round(time.monotonic() - started, 3)
    logger.info(
        "",
        extra={
            "event": "pipeline.run_done",
            "run_id": run_id,
            "risk_count": report["meta"]["risk_count"],
            "elapsed": elapsed,
            "tokens": report["meta"]["tokens"],
        },
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


def _run_batch(args) -> int:
    directory = Path(args.directory)
    if not directory.is_dir():
        raise SystemExit(f"目录不存在：{directory}")

    run_id = f"batch_{_new_run_id()}"
    log_path = setup_logging(run_id)
    started = time.monotonic()
    logger.info(
        "",
        extra={
            "event": "batch.run_start",
            "run_id": run_id,
            "directory": str(directory.resolve()),
            "workers": args.workers,
            "rps": args.rps,
            "max_inflight": args.max_inflight,
            "chaos": args.chaos,
            "log": str(log_path),
        },
    )

    throttle = RequestThrottle(args.rps, args.max_inflight)
    client = DeepSeekLLMClient(
        throttle=throttle,
        chaos=args.chaos,
        chaos_rate=args.chaos_rate,
    )
    trace_root = Path(f"trace_{run_id}") if args.trace else None

    summary = run_batch(
        directory,
        client,
        args.workers,
        run_id,
        parsers={
            "txt": TxtParser(),
            "docx": DocxParser(),
            "pdf": MinerUParser(),
        },
        trace_root=trace_root,
        trace_factory=TraceRecorder if args.trace else None,
    )
    elapsed = round(time.monotonic() - started, 3)
    summary["batch_elapsed"] = elapsed
    logger.info(
        "",
        extra={
            "event": "batch.run_done",
            "run_id": run_id,
            "ok": summary["ok"],
            "llm_failed": summary["llm_failed"],
            "failed": summary["failed"],
            "elapsed": elapsed,
            "tokens": summary["tokens"],
        },
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


def _new_run_id() -> str:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return f"{stamp}_{secrets.token_hex(3)}"


def _create_trace_dir(run_id: str) -> Path:
    base = Path(f"trace_{run_id}")
    candidate = base
    index = 1
    while candidate.exists():
        candidate = Path(f"{base}_{index}")
        index += 1
    candidate.mkdir(parents=True)
    return candidate


def _with_footer(text: str, *lines: str) -> str:
    footer = "\n".join(f"# {line}" for line in lines)
    return f"{text}\n\n{footer}\n"


if __name__ == "__main__":
    raise SystemExit(main())
