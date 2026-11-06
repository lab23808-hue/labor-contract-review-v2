"""上传文件的技术约束：文件名、扩展名、大小、文件头与 PDF 结构。

基础设施层实现，通过 domain.ports.UploadValidator 端口被应用层使用。
"""

from dataclasses import dataclass
from typing import Optional

from core.errors import ValidationError
from domain.ports import DocumentValidator

_MAGIC_PREFIXES = {
    "pdf": b"%PDF-",
    "docx": b"PK\x03\x04",
    "jpg": b"\xff\xd8\xff",
    "jpeg": b"\xff\xd8\xff",
    "png": b"\x89PNG\r\n\x1a\n",
}

_INVALID_FILENAME_PARTS = ("/", "\\", "..")


@dataclass(frozen=True)
class UploadPolicy:
    allowed_extensions: tuple[str, ...] = (
        "txt",
        "pdf",
        "docx",
        "jpg",
        "png",
        "jpeg",
    )
    max_upload_bytes: int = 2 * 1024 * 1024


DEFAULT_UPLOAD_POLICY = UploadPolicy()


class FileUploadValidator:
    def __init__(
        self,
        policy: UploadPolicy = DEFAULT_UPLOAD_POLICY,
        document_validator: Optional[DocumentValidator] = None,
    ) -> None:
        self._policy = policy
        self._document_validator = document_validator

    def validate(self, filename: str, content: bytes) -> str:
        return validate_upload(
            filename,
            content,
            self._document_validator,
            policy=self._policy,
        )


def validate_filename(filename: str) -> None:
    if not filename.strip():
        raise ValidationError("文件名为空")
    for part in _INVALID_FILENAME_PARTS:
        if part in filename:
            raise ValidationError(f"文件名包含非法路径字符 {part!r}: {filename!r}")


def extract_extension(filename: str) -> str:
    name = filename.strip()
    if "." not in name:
        return ""
    return name.rsplit(".", 1)[1].lower()


def validate_size(
    size_bytes: int,
    policy: UploadPolicy = DEFAULT_UPLOAD_POLICY,
) -> None:
    if size_bytes == 0:
        raise ValidationError("文件内容为空（0 字节）")
    if size_bytes > policy.max_upload_bytes:
        raise ValidationError(
            f"文件大小 {size_bytes} 字节超过上限 "
            f"{policy.max_upload_bytes} 字节"
        )


def validate_magic(file_format: str, content: bytes) -> None:
    prefix = _MAGIC_PREFIXES.get(file_format)
    if prefix is None:
        return
    if not content.startswith(prefix):
        raise ValidationError(
            f"文件头与扩展名不匹配：.{file_format} 文件必须以 {prefix!r} 开头"
        )


def validate_upload(
    filename: str,
    content: bytes,
    validator: Optional[DocumentValidator] = None,
    policy: UploadPolicy = DEFAULT_UPLOAD_POLICY,
) -> str:
    validate_filename(filename)

    file_format = extract_extension(filename)
    if file_format not in policy.allowed_extensions:
        allowed = ", ".join(
            f".{ext}" for ext in policy.allowed_extensions
        )
        actual = f".{file_format}" if file_format else "(无扩展名)"
        raise ValidationError(f"不支持的文件类型 {actual}，允许的类型：{allowed}")

    validate_size(len(content), policy)
    validate_magic(file_format, content)
    if file_format == "pdf" and validator is not None:
        validator.validate_pdf(content)
    return file_format
