# M7 结构化裁决字段重测

- 重测日期：2026-11-02
- 样本：M5 的 6 份合同、8 个文件，内容未改
- 调用方式：`python -m src.cli review <file>`，逐文件单独进程、单独一次 LLM 调用
- LLM 配置：`DEEPSEEK_MODEL=deepseek-flash`，`DEEPSEEK_THINKING=disabled`，`temperature=0`
- GT：沿用 M5/M6，不变
- 机制 commit：`6bc68c5`（输出格式块增加 `is_violation`；verifier 在 excerpt 校验后丢弃非 true 条目）

## 1. 命中总表（GT 不变）

| 文件 | GT 必须报 | GT 必须不报 | 实际 risks | 判定 |
|---|---|---|---|---|
| C-001.txt | 无 | 任何 risks | 无 | **命中** |
| C-002.txt | probation_exceed | — | probation_exceed、disguised_waiver | **命中** |
| C-002.docx | probation_exceed | — | 同 C-002.txt | **命中** |
| C-003.txt | illegal_penalty | probation_exceed | 无（两条 illegal_penalty 均被闸门丢弃） | **漏报** |
| C-004.txt | disguised_waiver（至少 4/5 两条） | social_security_missing | disguised_waiver ×3（第 4/5/6 条） | **命中** |
| C-005.txt | term_inconsistent | probation_exceed | term_inconsistent（rules） | **命中** |
| C-005.docx | term_inconsistent | probation_exceed | term_inconsistent（rules） | **命中** |
| C-006.txt | social_security_missing + wage_below_floor | — | social_security_missing（rules）、wage_below_floor（llm） | **命中** |

统计：

- 完整命中：**7 / 8**
- 漏报：**1**（C-003 的 illegal_penalty）
- 误报：**0**
- 管道故障：**0**

## 2. 预期与实际

| 预期 | 实际 | 结论 |
|---|---|---|
| C-001 零 risks | 零 risks；两条 LLM 条目（probation_exceed、wage_below_floor）均以 `is_violation=false` 被闸门丢弃 | 达成 |
| C-005 只剩 term_inconsistent（rules），wage_below_floor 消失 | txt/docx 均只剩 term_inconsistent；试用期条目以 false 被丢弃，本轮未再出现 wage_below_floor | 达成 |
| 其余六份结果不变 | C-002、C-002.docx、C-004、C-005.docx、C-006 不变；**C-003 变化** | C-003 未达成 |

## 3. 逐份摘要

### C-001（干净样本）

- risks：无。
- 闸门日志：2 条，均为 `is_violation=false`。
  - probation_exceed：「合同期限为36个月（满3年），试用期6个月，未超过对应上限6个月，不构成违规。」
  - wage_below_floor：「试用期工资16000元为约定工资18000元的约88.9%，不低于80%，不构成违规。」
- other_observations：无。
- 这是 M6 遗留误报的正面修复：LLM 在边界上仍会生成条目，但 `is_violation=false` 让它停在闸门外。

### C-002（试工期）

txt 与 docx 的 risk 集合一致：

| risk_type | source | excerpt |
|---|---|---|
| probation_exceed | llm | 乙方入职后前四个月为试工期 |
| disguised_waiver | llm | 试工期内甲方可随时以不符合要求为由解除本协议，无需支付补偿 |

- 两份的试用期工资条目都以 `is_violation=false` 被丢弃（7000/8500 ≈ 82.4%，未低于 80%）。
- txt 的 explanation 把一年期合同的上限写成「1 个月」（应为 2 个月），结论仍成立；属解释质量问题，不影响命中。
- other_observations：1 条，解除条件过于宽泛。

### C-003（普通离职违约金）——本轮漏报

- risks：无。required `illegal_penalty` 未出现在报告中。
- 闸门日志：两条 `illegal_penalty` 的 excerpt 均通过校验，但条目**完全缺少 `is_violation` 字段**，按「缺失视为 false」被丢弃：

```
{"risk_type": "illegal_penalty", "severity": "high", "excerpt": "乙方在合同期内提前离职的，应向甲方支付违约金人民币20000元", "explanation": "普通提前离职约定违约金，不属于专项培训服务期或竞业限制两种合法情形，属违法违约金条款。"}
{"risk_type": "illegal_penalty", "severity": "high", "excerpt": "乙方离职后未提前三十日书面通知甲方的，另行支付违约金5000元", "explanation": "对未提前通知离职约定违约金，同样不属于法定可约定违约金的两种情形。"}
```

- 归因：LLM 把同一违约金条款拆成两条，且在两条上都漏掉了 `is_violation`。verifier 按机制规定将字段缺失视为 false 丢弃。不是规则问题（rules 不覆盖 illegal_penalty），也不是合流问题。
- 机制层面这符合「宁可漏过可疑，不放过自相矛盾」的取舍；按纪律本轮不做当场补丁。

### C-004（免责话术）

| risk_type | source | excerpt |
|---|---|---|
| disguised_waiver | llm | 乙方自愿放弃缴纳社会保险…… |
| disguised_waiver | llm | 乙方在工作期间发生任何伤亡事故…… |
| disguised_waiver | llm | 因客户需求乙方自愿延长工作时间的，不另行支付加班费。 |

- 三条均带 `is_violation=true`，与 M6 一致。
- `social_security_missing` 未报，符合必须不报项。
- other_observations：1 条，工作地点由甲方单方安排。

### C-005（期限声明与日期区间矛盾）

| 文件 | risk_type | source |
|---|---|---|
| txt | term_inconsistent | rules |
| docx | term_inconsistent | rules |

- 声明 12 个月 vs 日期区间 36 个月，真矛盾，GT 要求报出，结果正确。
- 两份都没有 `probation_exceed`；试用期条目以 `is_violation=false` 被丢弃。
- 本轮 txt 未再生成 M6 那条 `wage_below_floor`；other_observations 各 1 条工资排除性记录。

### C-006（社保缺失 + 试用期工资，OCR 式断行）

| risk_type | source | excerpt |
|---|---|---|
| social_security_missing | rules | （规则生成，无 excerpt） |
| wage_below_floor | llm | 试用期间月工资为3200元 |

- 与 M6 一致。
- LLM 另提交了一条 `social_security_missing`，excerpt「（合同全文未见社会保险相关条款）」不在原文中，被第一道 excerpt 闸门丢弃；规则版已覆盖该 type。
- other_observations：1 条，工作地点随项目安排。

## 4. 与 M6 相比

| 项 | M6 | M7 |
|---|---|---|
| 完整命中 | 7 / 8 | 7 / 8 |
| 误报 | C-001 + C-005.txt 的 wage_below_floor | 0 |
| 漏报 | 0 | 1（C-003 illegal_penalty） |
| C-001 | probation_exceed 误报 | 零 risks |
| C-005.txt | term + wage_below_floor | 仅 term |

`is_violation` 闸门解决了 C-001 与 C-005.txt 的自相矛盾误报，同时在 C-003 上暴露了新的失效模式：LLM 漏字段时，真实风险会被静默丢弃。

## 5. 验收

- 测试：`101 passed, 1 deselected`（既有 99 + verifier 新增 2；slow 默认跳过）
- stability：`1 passed`，5 次运行的 `risk_type` 集合与 excerpt 完全一致；5 份原始输出已刷新入库
- 本轮改动面仅输出格式块与 verifier 闸门，未动卡片正文、规则、合流器
