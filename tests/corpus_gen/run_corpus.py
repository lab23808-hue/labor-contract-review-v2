"""Run the generated corpus through the real review pipeline.

This calls the real LLM client configured in .env; it is not a pytest test.

Usage:
    python tests/corpus_gen/run_corpus.py
    python tests/corpus_gen/run_corpus.py --observed
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from app.review import run_full_review
from domain.cleaning import clean_text
from domain.extractor import extract_fields
from infra.txt_parser import TxtParser

CORPUS = Path(__file__).resolve().parent
EXPECTED_PATH = CORPUS / "expected.json"
GENERATED_DIR = CORPUS / "generated"


def main(argv):
    expected = json.loads(EXPECTED_PATH.read_text(encoding="utf-8"))
    observed = {}
    matched = 0

    for name in sorted(expected):
        path = GENERATED_DIR / name
        text = clean_text(TxtParser().parse(name, path.read_bytes()))
        report = run_full_review(text, extract_fields(text))
        actual = [risk["risk_type"] for risk in report["risks"]]
        want = expected[name]["expected_risks"]
        ok = actual == want
        matched += int(ok)
        observed[name] = {
            "expected_risks": want,
            "actual_risks": actual,
            "match": ok,
        }
        print(
            f"{name} {'OK' if ok else 'MISMATCH'} "
            f"actual={actual} expected={want}"
        )

    print(f"matched {matched}/{len(expected)}")
    if "--observed" in argv:
        out_path = CORPUS / "observed.json"
        out_path.write_text(
            json.dumps(observed, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(f"wrote {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
