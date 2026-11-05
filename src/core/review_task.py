from dataclasses import dataclass
from typing import Any, Optional


@dataclass(frozen=True)
class ReviewTask:
    contract_id: str
    filename: str
    status: str
    result: Optional[dict[str, Any]]
    error: Optional[str]
    cleaned_text: Optional[str]
    created_at: float
    updated_at: float
