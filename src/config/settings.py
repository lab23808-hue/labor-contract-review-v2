import os

MAX_UPLOAD_BYTES = int(os.getenv("MAX_UPLOAD_BYTES", "10485760"))

ALLOWED_EXTENSIONS = tuple(
    ext.strip().lower().lstrip(".")
    for ext in os.getenv("ALLOWED_EXTENSIONS", "txt,pdf,docx").split(",")
    if ext.strip()
)

APP_ENV = os.getenv("APP_ENV", "dev")

DATA_DIR = os.getenv("DATA_DIR", "data")
