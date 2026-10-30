from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from core.errors import ContractNotFoundError, ValidationError
from domain.ports import DuplicateContractError


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(ValidationError)
    async def handle_validation_error(
        request: Request, exc: ValidationError
    ) -> JSONResponse:
        return JSONResponse(status_code=422, content={"detail": str(exc)})

    @app.exception_handler(DuplicateContractError)
    async def handle_duplicate_contract(
        request: Request, exc: DuplicateContractError
    ) -> JSONResponse:
        return JSONResponse(status_code=409, content={"detail": str(exc)})

    @app.exception_handler(ContractNotFoundError)
    async def handle_contract_not_found(
        request: Request, exc: ContractNotFoundError
    ) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": str(exc)})
