import asyncio
from contextlib import asynccontextmanager
from typing import AsyncIterator, Mapping, Optional

from fastapi import FastAPI

from api.errors import register_exception_handlers
from api.routes import contracts
from domain.ports import ContractRepository, DocumentParser, DocumentValidator
from infra.docx_parser import DocxParser
from infra.mineru_parser import MinerUParser
from infra.pdf_validator import PdfValidator
from infra.sqlite_repository import SQLiteContractRepository
from infra.txt_parser import TxtParser


def build_parsers() -> dict[str, DocumentParser]:
    mineru = MinerUParser()
    return {
        "txt": TxtParser(),
        "docx": DocxParser(),
        "pdf": mineru,
        "jpg": mineru,
        "png": mineru,
    }


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    app.state.contract_repository.reset_parsing_to_failed()
    yield


def create_app(
    repository: Optional[ContractRepository] = None,
    parsers: Optional[Mapping[str, DocumentParser]] = None,
    validator: Optional[DocumentValidator] = None,
) -> FastAPI:
    app = FastAPI(title="Labor Contract Review", lifespan=_lifespan)
    app.state.contract_repository = (
        repository if repository is not None else SQLiteContractRepository()
    )
    app.state.parsers = parsers if parsers is not None else build_parsers()
    app.state.validator = validator if validator is not None else PdfValidator()
    app.state.parse_semaphore = asyncio.Semaphore(1)
    register_exception_handlers(app)
    app.include_router(contracts.router)
    return app
