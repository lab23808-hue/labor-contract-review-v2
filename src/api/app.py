from contextlib import asynccontextmanager
from dataclasses import fields
from typing import AsyncIterator, Optional

from fastapi import FastAPI

from api.container import AppContainer, build_container
from api.errors import register_exception_handlers
from api.routes import contracts, review


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    app.state.contract_repository.reset_parsing_to_failed()
    yield
    app.state.review_queue.close()


def create_app(
    container: Optional[AppContainer] = None,
    **overrides,
) -> FastAPI:
    app = FastAPI(title="Labor Contract Review", lifespan=_lifespan)
    container = (
        container if container is not None else build_container(**overrides)
    )
    for field in fields(container):
        setattr(app.state, field.name, getattr(container, field.name))
    register_exception_handlers(app)
    app.include_router(contracts.router)
    app.include_router(review.router)
    return app
