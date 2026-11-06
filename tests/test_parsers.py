from io import BytesIO

import pytest
from docx import Document

from config import settings
from domain.ports import ParsingError
from infra.docx_parser import DocxParser
from infra.mineru_parser import MinerUParser
from infra.parsers import build_parsers, registry_mismatch
from infra.txt_parser import TxtParser


def test_txt_parser_normalizes_encoding_and_newlines():
    parser = TxtParser()

    assert parser.parse("a.txt", "劳动合同".encode("utf-8")) == "劳动合同"
    assert parser.parse("a.txt", "劳动合同".encode("gb18030")) == "劳动合同"
    assert parser.parse("a.txt", b"a\r\nb\rc") == "a\nb\nc"


def test_docx_parser_extracts_headings_paragraphs_and_tables():
    document = Document()
    document.add_heading("劳动合同", level=1)
    document.add_paragraph("第一条 试用期三个月。")
    table = document.add_table(rows=1, cols=2)
    table.rows[0].cells[0].text = "甲方"
    table.rows[0].cells[1].text = "乙方"
    buffer = BytesIO()
    document.save(buffer)

    markdown = DocxParser().parse("contract.docx", buffer.getvalue())

    assert "# 劳动合同" in markdown
    assert "第一条 试用期三个月。" in markdown
    assert "| 甲方 | 乙方 |" in markdown


def test_mineru_parser_reads_markdown_and_errors_when_missing(monkeypatch):
    parser = MinerUParser()

    def fake_run(input_path, output_path):
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text("# 解析结果\n\n正文", encoding="utf-8")

    monkeypatch.setattr(parser, "_run", fake_run)
    assert parser.parse("contract.pdf", b"%PDF-1.7") == "# 解析结果\n\n正文"

    monkeypatch.setattr(parser, "_run", lambda input_path, output_path: None)
    with pytest.raises(ParsingError):
        parser.parse("contract.pdf", b"%PDF-1.7")


def test_registry_mismatch_reports_both_directions():
    missing, unused = registry_mismatch(
        {"txt": TxtParser()}, ("txt", "pdf")
    )
    assert missing == ["pdf"]
    assert unused == []

    missing, unused = registry_mismatch(
        {"txt": TxtParser(), "jpg": TxtParser()}, ("txt",)
    )
    assert missing == []
    assert unused == ["jpg"]


def test_default_registry_covers_allowed_extensions():
    missing, unused = registry_mismatch(
        build_parsers(), settings.ALLOWED_EXTENSIONS
    )
    assert missing == []
    assert unused == []
