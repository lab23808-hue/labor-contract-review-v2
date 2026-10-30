from config import settings
from core.errors import ValidationError

_MAGIC_PREFIXES = {
    "pdf": b"%PDF-",
    "docx": b"PK\x03\x04",
}

_INVALID_FILENAME_PARTS = ("/", "\\", "..")


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


def validate_size(size_bytes: int) -> None:
    if size_bytes == 0:
        raise ValidationError("文件内容为空（0 字节）")
    if size_bytes > settings.MAX_UPLOAD_BYTES:
        raise ValidationError(
            f"文件大小 {size_bytes} 字节超过上限 {settings.MAX_UPLOAD_BYTES} 字节"
        )


def validate_magic(file_format: str, content: bytes) -> None:
    prefix = _MAGIC_PREFIXES.get(file_format)
    if prefix is None:
        return
    if not content.startswith(prefix):
        raise ValidationError(
            f"文件头与扩展名不匹配：.{file_format} 文件必须以 {prefix!r} 开头"
        )


def validate_upload(filename: str, content: bytes) -> str:
    validate_filename(filename)

    file_format = extract_extension(filename)
    if file_format not in settings.ALLOWED_EXTENSIONS:
        allowed = ", ".join(f".{ext}" for ext in settings.ALLOWED_EXTENSIONS)
        actual = f".{file_format}" if file_format else "(无扩展名)"
        raise ValidationError(f"不支持的文件类型 {actual}，允许的类型：{allowed}")

    validate_size(len(content))
    validate_magic(file_format, content)
    return file_format
