from typing import Iterable, Mapping

from domain.ports import DocumentParser
from infra.docx_parser import DocxParser
from infra.mineru_parser import MinerUParser
from infra.txt_parser import TxtParser


def build_parsers() -> dict[str, DocumentParser]:
    mineru = MinerUParser()
    return {
        "txt": TxtParser(),
        "docx": DocxParser(),
        "pdf": mineru,
        "jpg": mineru,
        "png": mineru,
        "jpeg": mineru,
    }


def registry_mismatch(
    parsers: Mapping[str, DocumentParser],
    allowed: Iterable[str],
) -> tuple[list[str], list[str]]:
    """返回 (白名单缺 parser 的扩展名, 注册表里未被白名单使用的扩展名)。"""
    allowed_set = {ext.lower().lstrip(".") for ext in allowed}
    parser_set = {name.lower().lstrip(".") for name in parsers}
    return sorted(allowed_set - parser_set), sorted(parser_set - allowed_set)
