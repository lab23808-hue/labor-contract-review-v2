import asyncio
import hashlib
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Mapping, Optional, Sequence

from core.contract import Contract
from core.errors import (
    ContractNotFoundError,
    CorruptedFileError,
    InvalidStateError,
    ValidationError,
)
from domain.cleaning import clean_text
from domain.ports import (
    ContractRepository,
    DocumentParser,
    DocumentValidator,
    DuplicateContractError,
    ParsingError,
    UnsupportedFormatError,
)
from domain.validation import (
    DEFAULT_UPLOAD_POLICY,
    UploadPolicy,
    validate_upload,
)


@dataclass(frozen=True)
class BatchItem:
    filename: str
    contract_id: Optional[str]
    status: int
    detail: str


class ContractManager:
    def __init__(
        self,
        repository: ContractRepository,
        parsers: Mapping[str, DocumentParser],
        validator: Optional[DocumentValidator] = None,
        policy: UploadPolicy = DEFAULT_UPLOAD_POLICY,
    ) -> None:
        self._repository = repository
        self._parsers = parsers
        self._validator = validator
        self._policy = policy

    def upload(
        self,
        filename: str,
        content: bytes,
        contract_id: Optional[str] = None,
    ) -> Contract:
        file_format = validate_upload(
            filename, content, self._validator, policy=self._policy
        )

        sha256 = hashlib.sha256(content).hexdigest()
        existing = self._repository.find_by_sha256(sha256)
        if existing is not None:
            raise DuplicateContractError(
                f"文件已存在：contract_id={existing.contract_id}，sha256={sha256}"
            )

        contract = Contract(
            contract_id=contract_id or uuid.uuid4().hex,
            filename=filename,
            file_format=file_format,
            sha256=sha256,
            size_bytes=len(content),
            created_at=datetime.now(timezone.utc),
        )
        self.save(contract, content)
        return contract

    def save(self, contract: Contract, content: bytes) -> None:
        self._repository.save(contract, content)

    def batch_upload(self, files: Sequence[tuple[str, bytes]]) -> list[BatchItem]:
        results: list[BatchItem] = []
        for filename, content in files:
            try:
                contract = self.upload(filename, content)
            except DuplicateContractError as exc:
                existing = self._repository.find_by_sha256(
                    hashlib.sha256(content).hexdigest()
                )
                existing_id = existing.contract_id if existing is not None else None
                results.append(BatchItem(filename, existing_id, 409, str(exc)))
            except CorruptedFileError as exc:
                results.append(BatchItem(filename, None, 400, str(exc)))
            except ValidationError as exc:
                results.append(BatchItem(filename, None, 422, str(exc)))
            else:
                results.append(
                    BatchItem(filename, contract.contract_id, 201, "uploaded")
                )
        return results

    def get(self, contract_id: str) -> Contract:
        contract = self._repository.get(contract_id)
        if contract is None:
            raise ContractNotFoundError(f"合同不存在：{contract_id}")
        return contract

    def list(self, limit: int = 20, offset: int = 0) -> list[Contract]:
        return self._repository.list(limit=limit, offset=offset)

    def exists_by_sha256(self, sha256: str) -> bool:
        return self._repository.exists_by_sha256(sha256)

    def delete(self, contract_id: str) -> None:
        self._repository.delete(contract_id)

    def parsed_artifact(self, contract_id: str) -> tuple[str, bytes]:
        contract = self._parsed_contract(contract_id)
        if contract.parsed_path is None:
            raise InvalidStateError(f"解析产物路径缺失：contract_id={contract_id}")
        return contract.filename, self._repository.read_artifact(contract.parsed_path)

    def cleaned_artifact(self, contract_id: str) -> tuple[str, bytes]:
        contract = self._parsed_contract(contract_id)
        if contract.cleaned_path is None:
            raise InvalidStateError(f"清洗产物路径缺失：contract_id={contract_id}")
        return contract.filename, self._repository.read_artifact(contract.cleaned_path)

    def _parsed_contract(self, contract_id: str) -> Contract:
        contract = self.get(contract_id)
        if contract.status != "parsed":
            raise InvalidStateError(
                f"合同尚未解析完成，无法下载产物：contract_id={contract_id}，status={contract.status}"
            )
        return contract

    def parse(self, contract_id: str) -> Contract:
        self.start_parse(contract_id)
        return self.execute_parse(contract_id)

    def start_parse(self, contract_id: str) -> Contract:
        if not self._repository.claim_for_parsing(contract_id):
            contract = self._repository.get(contract_id)
            if contract is None:
                raise ContractNotFoundError(f"合同不存在：{contract_id}")
            raise InvalidStateError(f"合同正在解析中，请稍后重试：{contract_id}")
        return self.get(contract_id)

    def execute_parse(self, contract_id: str) -> Contract:
        contract = self.get(contract_id)
        try:
            parser = self._parsers.get(contract.file_format)
            if parser is None:
                raise UnsupportedFormatError(f"不支持的解析格式：{contract.file_format}")

            parsed_markdown = parser.parse(
                contract.filename, self._repository.read_content(contract)
            )
            cleaned_text = clean_text(parsed_markdown)
            parsed_bytes = parsed_markdown.encode("utf-8")
            cleaned_bytes = cleaned_text.encode("utf-8")
            artifacts = self._repository.write_artifacts(
                contract, parsed_bytes, cleaned_bytes
            )
            parsed = contract.model_copy(
                update={
                    "status": "parsed",
                    "error": None,
                    "parsed_path": artifacts.parsed_path,
                    "parsed_sha256": hashlib.sha256(parsed_bytes).hexdigest(),
                    "cleaned_path": artifacts.cleaned_path,
                    "cleaned_sha256": hashlib.sha256(cleaned_bytes).hexdigest(),
                    "parsed_at": datetime.now(timezone.utc),
                }
            )
        except ParsingError as exc:
            self._repository.remove_artifacts(contract)
            failed = contract.model_copy(
                update={
                    "status": "failed",
                    "error": f"{type(exc).__name__}: {exc}",
                    "parsed_path": None,
                    "parsed_sha256": None,
                    "cleaned_path": None,
                    "cleaned_sha256": None,
                    "parsed_at": None,
                }
            )
            self._repository.update(failed)
            return failed

        self._repository.update(parsed)
        return parsed


async def run_parse_task(
    manager: ContractManager,
    contract_id: str,
    semaphore: asyncio.Semaphore,
    review_manager=None,
) -> None:
    async with semaphore:
        try:
            contract = await asyncio.to_thread(
                manager.execute_parse, contract_id
            )
        except Exception as exc:
            if review_manager is not None:
                review_manager.fail_reserved(
                    contract_id, f"{type(exc).__name__}: {exc}"
                )
            raise

    if review_manager is None:
        return
    if contract.status != "parsed":
        review_manager.fail_reserved(
            contract_id, contract.error or "解析失败"
        )
        return
    try:
        _, cleaned_bytes = manager.cleaned_artifact(contract_id)
        cleaned_text = cleaned_bytes.decode("utf-8")
    except Exception as exc:
        review_manager.fail_reserved(
            contract_id, f"{type(exc).__name__}: {exc}"
        )
        return
    review_manager.submit_reserved(contract_id, cleaned_text)
