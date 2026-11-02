# M11 竞业限制补偿的条款级作用域

- 日期：2026-11-02
- 改动面：`src/domain/rules/noncompete.py`（补偿检查作用域）
- 顺带修复：`src/logging_setup.py`（trace 摘要恢复到 console INFO 可见）
- prompt、LLM 卡片、merger、verifier 判定均未改

## 1. 作用域收窄

原逻辑在全文范围检查补偿关键词，条款外的正面提及会救回竞业限制条款里的
「无经济补偿」。M11 复用 M9 的「取包含竞业限制关键词的最长子句」机制：

1. 先定位竞业限制条款（最长相关子句，冒号/标点/换行切句，标题段独立）；
2. 只在该条款文本内检查补偿关键词；
3. M10 的 4 字符否定回看逻辑原样保留，只是输入从全文换成条款；
4. 条款内所有命中均被否定或无命中 → 报 `noncompete_no_pay`。

野题复现：

```
第十条 竞业限制：……竞业限制期限内无经济补偿。
第十六条 经济补偿：合同解除或终止时，经济补偿按工作年限计算。
```

第十六条不再计入，第十条报 `noncompete_no_pay`，excerpt 为第十条子句。

## 2. 测试

`138 passed, 1 deselected`（M-log 的 136 + 新增 2 项）。

新增：

- 野题复现：条款 A 否定补偿 + 条款 B 正面补偿 → 报，excerpt 不含条款 B
- 反向：补偿约定写在竞业限制条款内 → 不报

调整：

- M10 的「先否定后肯定」用例把肯定提及并入同一竞业限制子句，保持原语义
- 标题污染用例结果仍为不报：标题段自身含「竞业限制补偿」，它本身就是唯一
  命中的条款，补偿关键词仍在条款内。这是标题型条款的已知边界

## 3. 十份夹具重测

| 文件 | 实际 risks | 与 M10 对比 |
|---|---|---|
| C-001.txt | 无 | 一致 |
| C-002.txt | probation_exceed、disguised_waiver | 一致 |
| C-002.docx | 同 C-002.txt | 一致 |
| C-003.txt | illegal_penalty ×2（rules） | 一致 |
| C-004.txt | disguised_waiver ×3 | 一致 |
| C-005.txt | term_inconsistent（rules） | 一致 |
| C-005.docx | term_inconsistent（rules） | 一致 |
| C-006.txt | social_security_missing（rules）、wage_below_floor（llm） | 一致 |
| C-007.txt | open_ended_denied（llm） | 一致 |
| C-008.txt | noncompete_no_pay（rules）、noncompete_exceed（llm） | 一致 |

10/10 risk_type/source 集合不变；C-008 仍报 `noncompete_no_pay`，没有回归。

## 4. 已知边界（如实记录，不加启发式）

1. **条款间引用不跟进**：竞业限制条款只写「补偿标准见第 X 条」、实际补偿
   写在其他条款时，会漏判 `noncompete_no_pay`。本轮先固定为边界，是否处理
   留给批量压测的数据决定。
2. **标题自身含补偿关键词**：`竞业限制补偿：乙方放弃补偿…` 这类结构里，
   标题段是唯一命中的条款，且自身含「竞业限制补偿」，仍被视为已约定补偿。
3. **只检查最长的一个竞业限制子句**：若合同有多处竞业限制条款且补偿安排
   不同，只以最长子句为准。这是沿用 M9 的子句选择机制，未扩展。

## 5. trace 摘要恢复 console 可见

M-log 把 console 压到 WARNING 后，trace 摘要不再出现在终端。本次调整：

- console handler 级别提到 INFO，但增加过滤器：只放行 `pipeline.trace` 的
  INFO 事件，其余 INFO 仍只落文件；WARNING 及以上照常显示。
- console 格式对带 `detail` 的 trace 事件追加摘要文本。
- JSON 行日志格式不变；最终报告 JSON 不变。

C-006 实测 stderr：

```
... INFO pipeline.trace input path=... size=626 suffix=.txt
... INFO pipeline.trace parsed chars=250
... INFO pipeline.trace cleaned chars=243 removed=7
... INFO pipeline.trace rules {'probation': 0, ...}
... INFO pipeline.trace llm_raw risks=2 observations=1 failed=False
... INFO pipeline.trace llm_verified risks=1 observations=1 dropped=1
... INFO pipeline.trace merger risks=2 sources={'rules': 1, 'llm': 1} ...
... INFO pipeline.trace final risks=2 observations=1
```

`logs/review_<run_id>.log` 的字段集合与 M-log 完全一致。
