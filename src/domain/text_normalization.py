"""把解析器产出的 Markdown 规范化为审查使用的纯文本。

输入契约：必须是 parser 适配器输出的 Markdown（含标题/链接/图片语法）。
输出契约：无分页符、无 HTML/图片残留、空白归一化的纯文本，
作为 extractor 与 LLM prompt 的唯一输入。
"""

import re

_PAGE_NUMBER = re.compile(
    r"^\s*(?:第\s*\d+\s*页(?:\s*[/，,]\s*共\s*\d+\s*页)?"
    r"|[-—–]?\s*\d{1,4}\s*[-—–]?"
    r"|page\s+\d{1,4}(?:\s+of\s+\d{1,4})?)\s*$",
    re.IGNORECASE,
)
_HTML_RESIDUE = re.compile(r"<!--.*?-->|</?[a-zA-Z][^>]*>", re.DOTALL)
_IMAGE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_HEADING = re.compile(r"^\s{0,3}#{1,6}\s*", re.MULTILINE)
_EMPHASIS = re.compile(r"(\*\*|__|\*|_|~~|`)")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
_CJK = re.compile(r"[一-鿿]")


def normalize_text(markdown: str) -> str:
    text = markdown.lstrip("﻿")
    text = _HTML_RESIDUE.sub("", text)
    text = _IMAGE.sub("", text)
    text = _LINK.sub(r"\1", text)
    text = _HEADING.sub("", text)
    text = _EMPHASIS.sub("", text)

    kept = [
        line
        for line in text.splitlines()
        if not _PAGE_NUMBER.match(line) and not _is_garbage_line(line)
    ]
    return _normalize_whitespace("\n".join(kept))


def _is_garbage_line(line: str) -> bool:
    stripped = line.strip()
    if len(stripped) < 3:
        return False

    bad = sum(1 for char in stripped if char == "�" or _CONTROL.match(char))
    if bad / len(stripped) > 0.2:
        return True

    meaningful = sum(1 for char in stripped if char.isalnum() or _CJK.match(char))
    return meaningful == 0


def _normalize_whitespace(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace("　", " ").replace("\xa0", " ").replace("\t", "    ")

    lines = []
    for line in text.split("\n"):
        if not line.strip():
            lines.append("")
            continue
        indent = len(line) - len(line.lstrip(" "))
        body = re.sub(r" {2,}", " ", line.strip())
        lines.append(" " * min(indent, 8) + body)

    text = "\n".join(lines)
    return re.sub(r"\n{3,}", "\n\n", text).strip()
