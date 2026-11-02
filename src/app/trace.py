import json
import sys
from pathlib import Path
from typing import Any


class TraceRecorder:
    def __init__(self, directory: Path) -> None:
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.log_path = self.directory / "trace.log"

    def write_json(self, name: str, payload: Any) -> None:
        (self.directory / name).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def write_text(self, name: str, text: str) -> None:
        (self.directory / name).write_text(text, encoding="utf-8")

    def log(self, message: str) -> None:
        with self.log_path.open("a", encoding="utf-8") as handle:
            handle.write(message + "\n")
        print(f"[trace] {message}", file=sys.stderr)
