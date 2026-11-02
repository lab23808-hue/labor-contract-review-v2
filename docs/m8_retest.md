# M8 违约金确定性规则重测

- 重测日期：2026-11-02
- 样本：M5 的 6 份合同、8 个文件，内容未改
- 调用方式：`python -m src.cli review <file>`，逐文件单独进程、单独一次 LLM 调用
- GT：沿用 M5–M7，不变
- 模块 commit：`bf5164f`（penalty.py + 7 项单测）
- 接线 commit：本提交（review.py 接线、merger 多条规则保留、全链路测试与本文档）

## 1. 命中总表（GT 不变）

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

统计：

- 完整命中：**8 / 8**
- 漏报：**0**
- 误报：**0**
- 管道故障：**0**

## 2. C-003 逐条输出

| risk_type | severity | source | excerpt |
|---|---|---|---|
| illegal_penalty | medium | rules | 乙方在合同期内提前离职的，应向甲方支付违约金人民币20000元 |
| illegal_penalty | medium | rules | 乙方离职后未提前三十日书面通知甲方的，另行支付违约金5000元 |

- LLM 也提交了一条 `illegal_penalty`（把两条合并成一条，`is_violation=true`），合流时因 rules 已命中该 type 被丢弃，日志正常记录。
- 同 type 两条规则条目都保留，未互相折叠；`meta.sources = {"rules": 2, "llm": 0}`。
- other_observations：3 条，均为试用期工资、社保、期限的排除性记录。

## 3. 实现要点

### penalty 规则

- 先定位「违约金」出现处，允许字符间空白，因此 OCR 断行/断词仍能命中。
- 从出现处向两侧回溯到最近的句号、分号、换行或冒号，取该子句并移除空白作为 excerpt。
- 冒号作为边界是必要的：C-003 的条文标题「第五条 服务期与违约金：」本身含「服务期」，若与正文合并判断，会把第一条违法条款误判为合法服务期条款。标题段单独成句后，因含「服务期」被跳过，正文两段各报一条。
- 合法情形关键词：`服务期|培训|竞业限制|竞业禁止|保密`。命中任一即跳过。
- severity 固定 medium：关键词启发式无法完全排除「措辞特殊但实为合法服务期条款」的可能，按保护性偏向报告但降一档置信度。

### merger 调整

现有实现对 `rules_risks` 以 `risk_type` 为键做字典写入，同 type 的多条规则条目只剩最后一条，不满足「多条规则条目全部保留」。已改为：

- 用 `risk_type` 集合判断规则是否命中该 type；
- 规则条目按输入顺序全部保留；
- 规则命中时，该 type 的全部 LLM 条目逐条丢弃并记录日志；
- 规则沉默时，LLM 条目全部保留。

新增单测覆盖「同 type 两条规则条目都保留、同 type LLM 被丢弃」。

## 4. 其余七份

| 文件 | risks | 与 M7 对比 |
|---|---|---|
| C-001.txt | 无 | 不变（零风险） |
| C-002.txt | probation_exceed、disguised_waiver | 不变 |
| C-002.docx | 同 C-002.txt | 不变 |
| C-004.txt | disguised_waiver ×3 | 不变 |
| C-005.txt | term_inconsistent | 不变 |
| C-005.docx | term_inconsistent | 不变 |
| C-006.txt | social_security_missing、wage_below_floor | 不变 |

- C-001 的两条 LLM 条目仍以 `is_violation=false` 被闸门丢弃。
- C-002 的试用期工资条目同样以 false 被丢弃，risk 集合不变。
- C-006 的 LLM 社保条目仍因 excerpt 不在原文被第一道闸门丢弃，规则版覆盖。

## 5. 测试与回归

- 测试：`110 passed, 1 deselected`（M7 的 101 项 + penalty 单测 7 项 + merger 1 项 + C-003 全链路 1 项）
- C-003 全链路（FakeLLM）断言 `illegal_penalty` 恰好两条、`source=rules`、excerpt 与两条子句一致
- synthetic_0001 不含「违约金」，`judge_penalty` 返回空；既有全链路断言（probation_exceed + term_inconsistent，均 rules）不受影响
- stability 未重跑：synthetic_0001 不含违约金，prompt、verifier、LLM 快照均无变化
- prompt、verifier、现有规则与 LLM 的 illegal_penalty 卡片均未改动
