from typing import Optional

from fastapi import FastAPI

from api.errors import register_exception_handlers
from api.routes import contracts
from domain.ports import ContractRepository
from infra.sqlite_repository import SQLiteContractRepository


def create_app(repository: Optional[ContractRepository] = None) -> FastAPI:
    app = FastAPI(title="Labor Contract Review")
    app.state.contract_repository = (
        repository if repository is not None else SQLiteContractRepository()
    )
    register_exception_handlers(app)
    app.include_router(contracts.router)
    return app
