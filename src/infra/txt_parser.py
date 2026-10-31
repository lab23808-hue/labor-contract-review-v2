from domain.ports import ParsingError


class TxtParser:
    def parse(self, filename: str, content: bytes) -> str:
        try:
            return self._parse(content)
        except ParsingError:
            raise
        except Exception as exc:
            raise ParsingError(f"txt 解析失败：{exc}") from exc

    @staticmethod
    def _parse(content: bytes) -> str:
        try:
            text = content.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = content.decode("gb18030", errors="replace")
        return text.replace("\r\n", "\n").replace("\r", "\n")
