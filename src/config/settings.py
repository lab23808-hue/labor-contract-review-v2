import os

MAX_UPLOAD_BYTES = int(os.getenv("MAX_UPLOAD_BYTES", "10485760"))

ALLOWED_EXTENSIONS = tuple(
    ext.strip().lower().lstrip(".")
    for ext in os.getenv("ALLOWED_EXTENSIONS", "txt,pdf,docx").split(",")
    if ext.strip()
)

APP_ENV = os.getenv("APP_ENV", "dev")

DATA_DIR = os.getenv("DATA_DIR", "data")

MAX_BATCH_FILES = int(os.getenv("MAX_BATCH_FILES", "20"))

MAX_BATCH_BYTES = int(os.getenv("MAX_BATCH_BYTES", str(200 * 1024 * 1024)))
