import importlib.util
import json
from pathlib import Path

CORPUS_DIR = Path(__file__).parent / "corpus_gen"
GENERATOR_PATH = CORPUS_DIR / "generate.py"
EXPECTED_PATH = CORPUS_DIR / "expected.json"
GENERATED_DIR = CORPUS_DIR / "generated"


def _load_generator():
    spec = importlib.util.spec_from_file_location(
        "corpus_generate", GENERATOR_PATH
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_generated_corpus_is_in_sync_with_script_and_expected():
    contracts = _load_generator().build_contracts()
    expected = json.loads(EXPECTED_PATH.read_text(encoding="utf-8"))

    assert len(contracts) == 16
    assert set(contracts) == set(expected)
    for name, text in contracts.items():
        assert "defect" in expected[name]
        assert isinstance(expected[name]["expected_risks"], list)
        assert (GENERATED_DIR / name).read_text(encoding="utf-8") == text
