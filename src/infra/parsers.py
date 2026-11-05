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
    }
