class TxtParser:
    def parse(self, filename: str, content: bytes) -> str:
        try:
            text = content.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = content.decode("gb18030", errors="replace")
        return text.replace("\r\n", "\n").replace("\r", "\n")
