import json
import logging
import sys
from datetime import datetime
from pathlib import Path

_STANDARD_ATTRS = {
    "name",
    "msg",
    "args",
    "levelname",
    "levelno",
    "pathname",
    "filename",
    "module",
    "exc_info",
    "exc_text",
    "stack_info",
    "lineno",
    "funcName",
    "created",
    "msecs",
    "relativeCreated",
    "thread",
    "threadName",
    "processName",
    "process",
    "taskName",
    "message",
    "asctime",
}

_INSTALLED: list[logging.Handler] = []


class _EventFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        return hasattr(record, "event")


class _ConsoleFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if not hasattr(record, "event"):
            return False
        if record.levelno >= logging.WARNING:
            return True
        return record.event == "pipeline.trace"


class _RunIdFilter(logging.Filter):
    def __init__(self, run_id: str) -> None:
        super().__init__()
        self.run_id = run_id

    def filter(self, record: logging.LogRecord) -> bool:
        record.run_id = self.run_id
        return True


class _ConsoleFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        event = getattr(record, "event", record.name)
        line = (
            f"{self.formatTime(record, '%Y-%m-%d %H:%M:%S')} "
            f"{record.levelname} {event}"
        )
        detail = getattr(record, "detail", None)
        if detail:
            line = f"{line} {detail}"
        return line


class _JsonLineFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.fromtimestamp(record.created).isoformat(
                timespec="seconds"
            ),
            "level": record.levelname,
            "event": getattr(record, "event", record.name),
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key in _STANDARD_ATTRS or key.startswith("_"):
                continue
            if key in payload:
                continue
            payload[key] = value
        return json.dumps(payload, ensure_ascii=False, default=str)


def setup_logging(run_id: str) -> Path:
    log_dir = Path("logs")
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"review_{run_id}.log"

    root = logging.getLogger()
    reset_logging()
    root.setLevel(logging.DEBUG)

    console = logging.StreamHandler(sys.stderr)
    console.setLevel(logging.INFO)
    console.setFormatter(_ConsoleFormatter())
    console.addFilter(_ConsoleFilter())
    console.addFilter(_RunIdFilter(run_id))

    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(_JsonLineFormatter())
    file_handler.addFilter(_EventFilter())
    file_handler.addFilter(_RunIdFilter(run_id))

    root.addHandler(console)
    root.addHandler(file_handler)
    _INSTALLED.extend([console, file_handler])
    return log_path


def reset_logging() -> None:
    root = logging.getLogger()
    for handler in _INSTALLED:
        root.removeHandler(handler)
        handler.close()
    _INSTALLED.clear()
