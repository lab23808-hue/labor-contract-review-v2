"""Thin CLI: python -m src.cli review <file>."""

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

_SRC = Path(__file__).resolve().parent
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from app.review import run_full_review
from app.trace import TraceRecorder
from domain.cleaning import clean_text
from domain.extractor import extract_fields
from infra.docx_parser import DocxParser
from infra.txt_parser import TxtParser


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


def _read_cleaned(path: Path) -> str:
    return clean_text(_read_parsed(path))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m src.cli")
    subparsers = parser.add_subparsers(dest="command", required=True)
    review = subparsers.add_parser("review", help="对合同文件执行完整审查")
    review.add_argument("file")
    review.add_argument(
        "--trace",
        action="store_true",
        help="把各阶段中间产物写入 trace_<时间戳>/",
    )
    args = parser.parse_args(argv)

    if args.command != "review":
        return 1

    path = Path(args.file)
    if not path.is_file():
        raise SystemExit(f"文件不存在：{path}")

    trace_dir = None
    if args.trace:
        parsed_text = _read_parsed(path)
        cleaned_text = clean_text(parsed_text)
        trace_dir = _create_trace_dir()
        recorder = TraceRecorder(trace_dir)
        meta = {
            "path": str(path.resolve()),
            "size": path.stat().st_size,
            "suffix": path.suffix,
        }
        removed = len(parsed_text) - len(cleaned_text)
        recorder.write_json("00_input.meta.json", meta)
        recorder.write_text(
            "01_parsed.txt",
            _with_footer(parsed_text, f"字符数: {len(parsed_text)}"),
        )
        recorder.write_text(
            "02_cleaned.txt",
            _with_footer(
                cleaned_text,
                f"字符数: {len(cleaned_text)}",
                f"删除字符数: {removed}",
            ),
        )
        recorder.log(
            f"input path={meta['path']} size={meta['size']} "
            f"suffix={meta['suffix'] or '(none)'}"
        )
        recorder.log(f"parsed chars={len(parsed_text)}")
        recorder.log(f"cleaned chars={len(cleaned_text)} removed={removed}")
    else:
        cleaned_text = _read_cleaned(path)

    report = run_full_review(
        cleaned_text,
        extract_fields(cleaned_text),
        trace_dir=trace_dir,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


def _create_trace_dir() -> Path:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    base = Path(f"trace_{stamp}")
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
