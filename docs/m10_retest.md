# M10 否定式补偿探测

- 日期：2026-11-02
- 改动面：`src/domain/rules/noncompete.py`（补偿存在性判定）
- 附带修正：`src/app/review.py` 的 trace 仲裁计数（见第 4 节）
- prompt、LLM 卡片、merger、verifier 均未改动

## 1. 规则改动

补偿关键词（`经济补偿|竞业限制补偿|按月补偿`）的每次命中，向前回看最多
4 个字符；窗口内出现 `无|没有|不|放弃|无需|免于` 之一时，该命中视为
否定式提及，不计为补偿存在。只有至少一次非否定命中才视为已约定补偿；
所有命中均被否定或无命中时，报 `noncompete_no_pay`。

回看基于 OCR 归一化后的文本（移除全部空白），因此「无 经 济 补 偿」
与「无经济补偿」等价。

单测覆盖：

| 文本 | 结果 |
|---|---|
| 无经济补偿 | 报 |
| 无需支付经济补偿 | 报 |
| 按月支付经济补偿 | 不报 |
| 无经济补偿。甲方按月支付经济补偿 | 不报（肯定命中在场） |
| 无任何形式的额外经济补偿 | 不报（否定词距离 8 字符 > 4） |

既有「标题污染」用例行为不变：`竞业限制补偿：乙方放弃补偿…` 中
`竞业限制补偿` 位于开头、无否定前缀，仍被视为补偿存在而不报。

## 2. 十份夹具重测（GT 不变）

| 文件 | 实际 risks | 结果 |
|---|---|---|
| C-001.txt | 无 | 与 M9 一致 |
| C-002.txt | probation_exceed、disguised_waiver | 一致 |
| C-002.docx | 同 C-002.txt | 一致 |
| C-003.txt | illegal_penalty ×2（rules） | 一致 |
| C-004.txt | disguised_waiver ×3 | 一致 |
| C-005.txt | term_inconsistent（rules） | 一致 |
| C-005.docx | term_inconsistent（rules） | 一致 |
| C-006.txt | social_security_missing（rules）、wage_below_floor（llm） | 一致 |
| C-007.txt | open_ended_denied（llm） | 一致 |
| C-008.txt | noncompete_no_pay（rules）、noncompete_exceed（llm） | 一致 |

10/10 与 M9 完全一致，0 漏报、0 误报、0 管道故障。C-008 的
「无需支付任何补偿」仍由规则报出，未被否定式探测误伤。

## 3. 测试

`124 passed, 1 deselected`：

- M9 基线 117 项保持全绿
- noncompete 新增 5 项（否定式 / 无需支付 / 按月肯定 / 先否定后肯定 / 远距离否定）
- M-trace 既有 1 项
- trace 仲裁计数新增 1 项

stability 未重跑：prompt、verifier、LLM 卡片均无变化。

## 4. trace 仲裁计数疑点

**确认是计数器把非仲裁事件也算进去了。**

原实现：

```python
recorder.log("merger ... arbitration_events=%d" % len(arbitration))
```

`arbitration` 是 07_merger.json 里的完整事件流，包含三类：

- `keep_rule`：规则条目保留
- `keep_llm`：规则沉默时 LLM 条目保留
- `drop_llm_rule_hit`：双源命中时丢弃 LLM 条目（真正的仲裁动作）

C-003 的 trace 里 `arbitration_events=3`，实际构成是：

| 事件 | 数量 |
|---|---:|
| keep_rule（20000 元、5000 元两条规则） | 2 |
| drop_llm_rule_hit（LLM 合并版一条） | 1 |
| 真正的仲裁动作 | **1** |

修正后只统计 `drop_` 前缀事件，并在日志里同时保留总数：

```
merger risks=2 sources={'rules': 2, 'llm': 0} arbitration_events=1 total_events=3
```

C-006 的 trace 则是 `arbitration_events=0 total_events=2`（一条 keep_rule +
一条 keep_llm，无仲裁动作）。07_merger.json 的完整事件流不变，仍可看到
每条 keep/drop 的具体条目。
