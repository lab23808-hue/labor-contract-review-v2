from domain.text_normalization import normalize_text


def test_rule1_removes_page_numbers_residue_and_markdown_symbols():
    markdown = (
        "<!-- page 1 -->\n"
        "# 劳动合同\n\n"
        "**第一条** 试用期三个月。\n\n"
        "第 3 页\n\n"
        "![图](img.png)\n\n"
        "[链接文字](https://example.com)\n"
    )

    cleaned = normalize_text(markdown)

    assert "劳动合同" in cleaned
    assert "第一条 试用期三个月。" in cleaned
    assert "<!--" not in cleaned
    assert "#" not in cleaned
    assert "**" not in cleaned
    assert "第 3 页" not in cleaned
    assert "![图]" not in cleaned
    assert "链接文字" in cleaned


def test_rule2_normalizes_whitespace_and_blank_lines():
    markdown = "第一条　试用期　　三个月。   \n\n\n\n第二条 工资。\n"

    cleaned = normalize_text(markdown)

    assert "　" not in cleaned
    assert "\n\n\n" not in cleaned
    assert cleaned == "第一条 试用期 三个月。\n\n第二条 工资。"


def test_rule3_removes_ocr_garbage_lines():
    garbage = chr(0xFFFD) * 4 + "乱码"
    markdown = f"第一条 正常内容。\n{garbage}\n第二条 正常内容。"

    cleaned = normalize_text(markdown)

    assert "第一条 正常内容。" in cleaned
    assert "第二条 正常内容。" in cleaned
    assert "乱码" not in cleaned


def test_rule4_preserves_clause_numbering_and_heading_order():
    markdown = (
        "# 第一章 总则\n\n"
        "第一条 本协议自签署之日起生效。\n\n"
        "1. 子条款一\n"
        "2. 子条款二"
    )

    cleaned = normalize_text(markdown)

    assert cleaned.splitlines()[0] == "第一章 总则"
    assert "第一条 本协议自签署之日起生效。" in cleaned
    assert "1. 子条款一" in cleaned
    assert "2. 子条款二" in cleaned
