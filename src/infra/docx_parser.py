from io import BytesIO

from docx import Document

from domain.ports import ParsingError


class DocxParser:
    def parse(self, filename: str, content: bytes) -> str:
        try:
            return self._parse(content)
        except ParsingError:
            raise
        except Exception as exc:
            raise ParsingError(f"docx 解析失败：{exc}") from exc

    def _parse(self, content: bytes) -> str:
        document = Document(BytesIO(content))
        blocks: list[str] = []

        for paragraph in document.paragraphs:
            text = paragraph.text.strip()
            if not text:
                continue
            blocks.append(self._format_paragraph(paragraph, text))

        for table in document.tables:
            for row in table.rows:
                cells = [cell.text.strip() for cell in row.cells]
                blocks.append("| " + " | ".join(cells) + " |")

        return "\n\n".join(blocks)

    @staticmethod
    def _format_paragraph(paragraph, text: str) -> str:
        style_id = (getattr(paragraph.style, "style_id", "") or "").lower()
        if style_id == "title":
            return f"# {text}"
        if style_id.startswith("heading"):
            suffix = style_id.removeprefix("heading").strip()
            level = int(suffix) if suffix.isdigit() else 1
            return f"{'#' * min(level, 6)} {text}"
        return text
