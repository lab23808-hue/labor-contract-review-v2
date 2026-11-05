import hashlib
from datetime import datetime, timezone
from typing import Mapping

from core.contract import Contract
from core.errors import ContractNotFoundError, InvalidStateError
from domain.cleaning import clean_text
from domain.ports import (
    ContractRepository,
    DocumentParser,
    ParsingError,
    UnsupportedFormatError,
)


class ParsingService:
    def __init__(
        self,
        repository: ContractRepository,
        parsers: Mapping[str, DocumentParser],
    ) -> None:
        self._repository = repository
        self._parsers = parsers

    def start(self, contract_id: str) -> Contract:
        if not self._repository.claim_for_parsing(contract_id):
            contract = self._repository.get(contract_id)
            if contract is None:
                raise ContractNotFoundError(f"合同不存在：{contract_id}")
            raise InvalidStateError(
                f"合同正在解析中，请稍后重试：{contract_id}"
            )
        return self._get(contract_id)

    def parse(self, contract_id: str) -> Contract:
        self.start(contract_id)
        return self.execute(contract_id)

    def execute(self, contract_id: str) -> Contract:
        contract = self._get(contract_id)
        try:
            parser = self._parsers.get(contract.file_format)
            if parser is None:
                raise UnsupportedFormatError(
                    f"不支持的解析格式：{contract.file_format}"
                )

            parsed_markdown = parser.parse(
                contract.filename,
                self._repository.read_content(contract),
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
                    "parsed_sha256": hashlib.sha256(
                        parsed_bytes
                    ).hexdigest(),
                    "cleaned_path": artifacts.cleaned_path,
                    "cleaned_sha256": hashlib.sha256(
                        cleaned_bytes
                    ).hexdigest(),
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

    def _get(self, contract_id: str) -> Contract:
        contract = self._repository.get(contract_id)
        if contract is None:
            raise ContractNotFoundError(f"合同不存在：{contract_id}")
        return contract
