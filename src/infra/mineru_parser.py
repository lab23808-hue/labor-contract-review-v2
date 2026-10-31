import subprocess
import sys
import tempfile
from pathlib import Path

from domain.ports import ParsingError


class MinerUParser:
    def parse(self, filename: str, content: bytes) -> str:
        try:
            return self._parse(filename, content)
        except ParsingError:
            raise
        except Exception as exc:
            raise ParsingError(f"MinerU 解析失败：{exc}") from exc

    def _parse(self, filename: str, content: bytes) -> str:
        with tempfile.TemporaryDirectory(prefix="mineru-") as work:
            root = Path(work)
            input_path = root / "input" / filename
            input_path.parent.mkdir(parents=True, exist_ok=True)
            input_path.write_bytes(content)

            output_path = root / "output" / "parsed.md"
            self._run(input_path, output_path)
            if not output_path.exists():
                raise ParsingError("MinerU 解析失败：未产出 Markdown 文件")
            return output_path.read_text(encoding="utf-8", errors="replace")

    def _run(self, input_path: Path, output_path: Path) -> None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        script = Path(sys.executable).with_name(
            "mineru.exe" if sys.platform == "win32" else "mineru"
        )
        command = (
            [str(script)] if script.exists() else [sys.executable, "-m", "mineru"]
        )
        command += [
            "parse",
            str(input_path),
            "--output",
            str(output_path),
            "--format",
            "markdown",
            "--pages",
            "all",
            "--wait",
            "600",
        ]

        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=660,
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError("解析超时（660s）：MinerU 未在限定时间内完成") from exc

        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "").strip()[-800:]
            raise ParsingError(
                f"MinerU 解析失败（exit {result.returncode}）：{detail}"
            )
