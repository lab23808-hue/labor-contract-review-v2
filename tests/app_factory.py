"""测试专用组装根：把简写参数映射到 create_app 的 review_* 参数。

生产代码只有 api.app.create_app 一个入口；这里只是为了方便测试
注入 FakeLLM、临时目录与较小的队列参数。测试本身就是组装根，
因此允许直接构造 SQLiteContractRepository。
"""

import tempfile
from pathlib import Path
from typing import Any, Mapping, Optional

from api.app import create_app
from domain.ports import DocumentParser
from infra.sqlite_repository import SQLiteContractRepository

_OVERRIDES = {
    "client": "review_client",
    "store_path": "review_store_path",
    "workers": "review_workers",
    "max_inflight": "review_max_inflight",
    "rps": "review_rps",
    "queue_limit": "review_queue_limit",
    "rate_limit": "review_rate_limit",
    "rate_window": "review_rate_window",
    "ttl_seconds": "review_ttl_seconds",
    "cleanup_interval": "review_cleanup_interval",
}


def make_app(
    client: Any = None,
    parsers: Optional[Mapping[str, DocumentParser]] = None,
    store_path: Path | str = ":memory:",
    data_dir: Optional[Path | str] = None,
    **overrides: Any,
):
    if data_dir is None:
        if str(store_path) == ":memory:":
            data_dir = tempfile.mkdtemp(prefix="review-contracts-")
        else:
            data_dir = Path(store_path).parent / "contracts"
    kwargs = {_OVERRIDES[key]: value for key, value in overrides.items()}
    return create_app(
        repository=SQLiteContractRepository(data_dir),
        parsers=parsers,
        review_client=client,
        review_store_path=str(store_path),
        **kwargs,
    )
