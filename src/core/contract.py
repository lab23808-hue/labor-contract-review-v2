from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel

PARSABLE_STATUSES = frozenset({"uploaded", "parsed", "failed"})


class Contract(BaseModel):
    contract_id: str
    filename: str
    file_format: str
    sha256: str
    size_bytes: int
    status: Literal["uploaded", "parsing", "parsed", "failed"] = "uploaded"
    error: Optional[str] = None
    created_at: datetime
    parsed_path: Optional[str] = None
    parsed_sha256: Optional[str] = None
    cleaned_path: Optional[str] = None
    cleaned_sha256: Optional[str] = None
    parsed_at: Optional[datetime] = None
