from io import BytesIO

from pypdf import PdfReader

from core.errors import CorruptedFileError


class PdfValidator:
    def validate_pdf(self, content: bytes) -> None:
        try:
            reader = PdfReader(BytesIO(content))
            encrypted = reader.is_encrypted
            pages = 0 if encrypted else len(reader.pages)
        except Exception as exc:
            raise CorruptedFileError(f"PDF 内容损坏，无法打开：{exc}") from exc

        if encrypted:
            raise CorruptedFileError("PDF 已加密，无法解析")
        if pages == 0:
            raise CorruptedFileError("PDF 页数为 0")
