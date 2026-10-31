from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from core.errors import (
    ArtifactMissingError,
    ContractNotFoundError,
    CorruptedFileError,
    InvalidStateError,
    ValidationError,
)
from domain.ports import DuplicateContractError

# 注册异常处理器，挂这个函数把六段“异常处理规则”挂到 app 上。
# 以后只要程序里抛出这六种异常，FastAPI 就会自动拦截，
# 不会让程序崩溃，而是返回一个标准的 JSON 响应。
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

    @app.exception_handler(InvalidStateError)
    async def handle_invalid_state(
        request: Request, exc: InvalidStateError
    ) -> JSONResponse:
        return JSONResponse(status_code=409, content={"detail": str(exc)})

    @app.exception_handler(ArtifactMissingError)
    async def handle_artifact_missing(
        request: Request, exc: ArtifactMissingError
    ) -> JSONResponse:
        return JSONResponse(status_code=500, content={"detail": str(exc)})

    @app.exception_handler(CorruptedFileError)
    async def handle_corrupted_file(
        request: Request, exc: CorruptedFileError
    ) -> JSONResponse:
        return JSONResponse(status_code=400, content={"detail": str(exc)})
