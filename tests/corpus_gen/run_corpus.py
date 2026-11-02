"""Run the generated corpus through the real review pipeline.

This explicitly constructs the configured DeepSeek client; it is not a
pytest test and it does not use FakeLLM.

Usage:
    python tests/corpus_gen/run_corpus.py
    python tests/corpus_gen/run_corpus.py --observed
"""

import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from app.review import run_full_review
from config.llm import get_llm_config
from domain.cleaning import clean_text
from domain.extractor import extract_fields
from infra.llm_client import DeepSeekLLMClient
from infra.txt_parser import TxtParser
from logging_setup import reset_logging, setup_logging

CORPUS = Path(__file__).resolve().parent
EXPECTED_PATH = CORPUS / "expected.json"
GENERATED_DIR = CORPUS / "generated"


def main(argv):
    config = get_llm_config()
    run_id = f"corpus_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    log_path = setup_logging(run_id)
    print(f"LLM model={config.model} base_url={config.base_url}")
    print(f"run_id={run_id}")
    print(f"log={log_path.resolve()}")
    client = DeepSeekLLMClient(config)

    expected = json.loads(EXPECTED_PATH.read_text(encoding="utf-8"))
    observed = {}
    matched = 0

    try:
        for name in sorted(expected):
            path = GENERATED_DIR / name
            text = clean_text(TxtParser().parse(name, path.read_bytes()))
            print(f"{name}: calling LLM...", end=" ", flush=True)
            report = run_full_review(
                text, extract_fields(text), llm_client=client
            )
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
                f"llm_failed={report['meta']['llm_failed']} "
                f"{'OK' if ok else 'MISMATCH'} "
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
    finally:
        reset_logging()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
