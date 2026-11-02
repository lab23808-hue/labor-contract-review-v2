# M9 扩卡片轮重测

- 重测日期：2026-11-02
- 样本：原 6 份合同 8 个文件 + 新增 C-007 / C-008，共 10 个文件
- 调用方式：`python -m src.cli review <file>`，逐文件单独进程、单独一次 LLM 调用
- LLM 配置：`DEEPSEEK_MODEL=deepseek-flash`，`DEEPSEEK_THINKING=disabled`，`temperature=0`
- GT：旧 8 份沿用 M5–M8；C-007 / C-008 按 M9 指令
- R1 commit：`749d81a`（noncompete 规则 + 单测 + 接线）
- B1 commit：本提交（第 7/8 张卡片、prompt/stability 快照、C-007/C-008、全链路测试与本文档）

## 1. 十份文件命中总表

| 文件 | GT 必须报 | GT 必须不报 | 实际 risks | 判定 |
|---|---|---|---|---|
| C-001.txt | 无 | 任何 risks | 无 | **命中** |
| C-002.txt | probation_exceed | — | probation_exceed、disguised_waiver | **命中** |
| C-002.docx | probation_exceed | — | 同 C-002.txt | **命中** |
| C-003.txt | illegal_penalty | probation_exceed | illegal_penalty ×2（rules） | **命中** |
| C-004.txt | disguised_waiver（至少 4/5 两条） | social_security_missing | disguised_waiver ×3（第 4/5/6 条） | **命中** |
| C-005.txt | term_inconsistent | probation_exceed | term_inconsistent（rules） | **命中** |
| C-005.docx | term_inconsistent | probation_exceed | term_inconsistent（rules） | **命中** |
| C-006.txt | social_security_missing + wage_below_floor | — | social_security_missing（rules）、wage_below_floor（llm） | **命中** |
| C-007.txt | open_ended_denied | 其他 | open_ended_denied（llm） | **命中** |
| C-008.txt | noncompete_no_pay（rules）+ noncompete_exceed（LLM 命中为佳） | 其他 | noncompete_no_pay（rules）、noncompete_exceed（llm） | **命中**（LLM 期限项命中） |

统计：

- 完整命中：**10 / 10**
- 漏报：**0**
- 误报：**0**
- 管道故障：**0**
- 旧 8 份与 M8 相比：risk_type 集合与 source 全部一致，零回归

## 2. C-007（无固定期限权利排除）

| risk_type | severity | source | excerpt |
|---|---|---|---|
| open_ended_denied | medium | llm | 本合同期满后如续签，仍为固定期限劳动合同，乙方不得要求签订无固定期限劳动合同。 |

- 规则侧无对应模块，命中完全来自第 7 张卡片。
- 其余风险项均未触发：三年期 + 3 个月试用期合法，社保条款在场，无违约金、无竞业限制。
- other_observations：无。

## 3. C-008（竞业限制无补偿 + 超期）

| risk_type | severity | source | excerpt |
|---|---|---|---|
| noncompete_no_pay | high | rules | 乙方离职后三年内不得入职与甲方有竞争关系的单位，竞业限制期间甲方无需支付任何补偿 |
| noncompete_exceed | medium | llm | 乙方离职后三年内不得入职与甲方有竞争关系的单位，竞业限制期间甲方无需支付任何补偿。 |

- 规则侧：「无需支付任何补偿」不包含 `经济补偿|竞业限制补偿|按月补偿` 任一关键词，未被变体话术骗过，正确报 `noncompete_no_pay`。
- LLM 侧：第 8 张卡片正确报出 3 年 > 2 年，且未把补偿问题重复放进 risks；仅在 other_observations 留了一条补偿提示。
- excerpt 取包含「竞业限制」的最长子句，避免只抓到独立标题「第六条 竞业限制」。
- 其余风险项均未触发：二年期 + 2 个月试用期合法，社保条款在场，无违约金。
- other_observations：1 条，竞业限制补偿提示。

## 4. noncompete 规则的已知边界

单测固定了标题污染行为：

```
竞业限制补偿：乙方放弃补偿，甲方无需支付。
```

全文含「竞业限制」且含「竞业限制补偿」，按封闭的全文关键词判定，规则**不报**。这是「全文关键词」启发式的已知边界，本轮按要求如实记录，未加额外启发式。C-008 不受影响，因为它的文本只有「任何补偿」，不命中补偿关键词组。

## 5. 快照与稳定性

- prompt 快照已按第 7/8 张卡片重录；前六条一字未动，输出格式块未改。
- stability：`1 passed`，5 次运行的 `risk_type` 集合与 excerpt 完全一致，结论集与 M8 相同（probation_exceed + term_inconsistent，均 `is_violation=true`）；5 份原始输出已刷新入库。
- synthetic_0001 不含无固定期限与竞业限制条款，新卡片未引入额外结论。

## 6. 测试

- `117 passed, 1 deselected`（M8 的 110 项 + noncompete 单测 5 项 + C-007/C-008 全链路 2 项）
- C-007 全链路：FakeLLM 返回 `open_ended_denied`，断言保留且 `source=llm`
- C-008 全链路：规则 `noncompete_no_pay` 与 LLM `noncompete_exceed` 同时保留，`sources={rules:1, llm:1}`
- merger、verifier 零改动；旧夹具与 GT 未改
