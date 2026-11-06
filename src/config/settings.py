import os

MAX_UPLOAD_BYTES = int(
    os.getenv("MAX_UPLOAD_BYTES", str(2 * 1024 * 1024))
)

ALLOWED_EXTENSIONS = tuple(
    ext.strip().lower().lstrip(".")
    for ext in os.getenv(
        "ALLOWED_EXTENSIONS", "txt,pdf,docx,jpg,png,jpeg"
    ).split(",")
    if ext.strip()
)

APP_ENV = os.getenv("APP_ENV", "dev")

DATA_DIR = os.getenv("DATA_DIR", "data")

MAX_BATCH_FILES = int(os.getenv("MAX_BATCH_FILES", "20"))

MAX_BATCH_BYTES = int(os.getenv("MAX_BATCH_BYTES", str(200 * 1024 * 1024)))

REVIEW_STORE_PATH = os.getenv(
    "REVIEW_STORE_PATH", str(os.path.join(DATA_DIR, "review_tasks.sqlite"))
)

REVIEW_QUEUE_LIMIT = int(os.getenv("REVIEW_QUEUE_LIMIT", "50"))

REVIEW_RATE_LIMIT = int(os.getenv("REVIEW_RATE_LIMIT", "5"))

REVIEW_RATE_WINDOW = float(os.getenv("REVIEW_RATE_WINDOW", "60"))

REVIEW_TTL_SECONDS = float(
    os.getenv("REVIEW_TTL_SECONDS", str(24 * 3600))
)

REVIEW_CLEANUP_INTERVAL = float(
    os.getenv("REVIEW_CLEANUP_INTERVAL", "600")
)

REVIEW_WORKERS = int(os.getenv("REVIEW_WORKERS", "8"))

REVIEW_MAX_INFLIGHT = int(os.getenv("REVIEW_MAX_INFLIGHT", "8"))

REVIEW_RPS = float(os.getenv("REVIEW_RPS", "8"))

