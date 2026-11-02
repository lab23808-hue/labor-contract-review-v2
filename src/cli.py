"""Thin CLI: python -m src.cli review <file>."""

import argparse
import json
import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parent
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from app.review import run_full_review
from domain.cleaning import clean_text
from domain.extractor import extract_fields
from infra.docx_parser import DocxParser
from infra.txt_parser import TxtParser


def _read_cleaned(path: Path) -> str:
    suffix = path.suffix.lower()
    content = path.read_bytes()
    if suffix == ".txt":
        return clean_text(TxtParser().parse(path.name, content))
    if suffix == ".docx":
        return clean_text(DocxParser().parse(path.name, content))
    if suffix == ".pdf":
        from infra.mineru_parser import MinerUParser

        return clean_text(MinerUParser().parse(path.name, content))
    raise SystemExit(f"不支持的文件类型：{suffix or '(无扩展名)'}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m src.cli")
    subparsers = parser.add_subparsers(dest="command", required=True)
    review = subparsers.add_parser("review", help="对合同文件执行完整审查")
    review.add_argument("file")
    args = parser.parse_args(argv)

    if args.command != "review":
        return 1

    path = Path(args.file)
    if not path.is_file():
        raise SystemExit(f"文件不存在：{path}")

    cleaned_text = _read_cleaned(path)
    report = run_full_review(cleaned_text, extract_fields(cleaned_text))
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
