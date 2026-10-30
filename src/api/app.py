from typing import Optional

from fastapi import FastAPI

from api.dependencies import TempFileUploadSink
from api.errors import register_exception_handlers
from api.routes import contracts
from domain.ports import UploadSink


def create_app(sink: Optional[UploadSink] = None) -> FastAPI:
    app = FastAPI(title="Labor Contract Review")
    app.state.upload_sink = sink if sink is not None else TempFileUploadSink()
    register_exception_handlers(app)
    app.include_router(contracts.router)
    return app
