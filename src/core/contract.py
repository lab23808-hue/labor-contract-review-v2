from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel


class Contract(BaseModel):
    contract_id: str
    filename: str
    file_format: str
    sha256: str
    size_bytes: int
    status: Literal["uploaded"] = "uploaded"
    error: Optional[str] = None
    created_at: datetime
