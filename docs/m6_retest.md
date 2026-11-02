# M6 修复轮重测

- 重测日期：2026-11-02
- 样本：M5 的 6 份合同、8 个文件，内容未改
- 调用方式：`python -m src.cli review <file>`，逐文件单独进程、单独一次 LLM 调用
- LLM 配置：`DEEPSEEK_MODEL=deepseek-flash`，`DEEPSEEK_THINKING=disabled`，`temperature=0`
- GT：沿用 M5，不变

## 1. 修复项与提交

| 项 | 内容 | commit |
|---|---|---|
| A1 | 日期月差：截止日 +1 天后再算月差，日不足再减 1 | `099ce08` |
| A2 | verifier 比对前移除全部空白，断行 excerpt 不再误杀 | `e40a8fc` |
| A3 | merger 按 risk_type 分组：规则沉默时保留该 type 全部 LLM 条目 | `1995595` |
| B1 | probation 卡片追加「经计算未超过对应上限的，不得将该条目列入 risks。」；prompt 与 stability 快照重录 | `86a8524` |

## 2. 命中总表（GT 不变）

| 文件 | GT 必须报 | GT 必须不报 | 实际 risks | 判定 |
|---|---|---|---|---|
| C-001.txt | 无 | 任何 risks | probation_exceed（llm） | **误报** |
| C-002.txt | probation_exceed | — | probation_exceed（llm）、disguised_waiver（llm） | **命中** |
| C-002.docx | probation_exceed | — | 同 C-002.txt | **命中** |
| C-003.txt | illegal_penalty | probation_exceed | illegal_penalty（llm） | **命中** |
| C-004.txt | disguised_waiver（至少 4/5 两条） | social_security_missing | disguised_waiver ×3（llm：第 4/5/6 条） | **命中** |
| C-005.txt | term_inconsistent | probation_exceed | term_inconsistent（rules）、wage_below_floor（llm） | **命中**；附 1 条 GT 未约束的误报 |
| C-005.docx | term_inconsistent | probation_exceed | term_inconsistent（rules） | **命中** |
| C-006.txt | social_security_missing + wage_below_floor | — | social_security_missing（rules）、wage_below_floor（llm） | **命中** |

统计：

- 完整命中：**7 / 8**
- 漏报：**0**
- 管道故障：**0**
- 误报：**1**（C-001，零风险样本仍报 probation_exceed）
- 另有 1 条 GT 未约束的误报：C-005.txt 的 `wage_below_floor`

## 3. 逐份摘要

### C-001（干净样本）

| risk_type | severity | source | excerpt |
|---|---|---|---|
| probation_exceed | high | llm | 其中试用期自2025年3月1日起至2025年8月31日止 |

- LLM 的 explanation 自述「恰好等于法定上限 6 个月，未超过，故不应列入风险」，却仍留在 `risks`。B1 的追加句没有改变这一条的输出。
- 首次 LLM 返回不是合法 JSON（`Extra data: line 3 column 1`），客户端按既有逻辑重试，第二次成功；`meta.llm_failed=false`。
- other_observations：1 条，试用期工资 16000/18000 ≈ 88.9%，未低于 80%。
- 归因：LLM 在「上限恰好相等」的边界上仍输出风险条目，且自述与输出自相矛盾；verifier 只校验 excerpt 是否在原文中，不校验结论是否被 excerpt 支持。按纪律本轮不再追加第二个 prompt 补丁。

### C-002（试工期）

txt 与 docx 的 risk 集合一致：

| risk_type | source | excerpt |
|---|---|---|
| probation_exceed | llm | 乙方入职后前四个月为试工期 |
| disguised_waiver | llm | 试工期内甲方可随时以不符合要求为由解除本协议，无需支付补偿 |

- A1 后日期区间 2025-04-01→2026-03-31 推算为 12 个月，与声明 12 个月一致，M5 的额外 `term_inconsistent` 已消失。
- 规则分支仍不识别「试工期」，`probation_exceed` 继续由 LLM 单源报出。
- other_observations：试工期工资 7000/8500 ≈ 82.4%，未低于 80%。

### C-003（普通离职违约金）

| risk_type | source | excerpt |
|---|---|---|
| illegal_penalty | llm | 乙方在合同期内提前离职的，应向甲方支付违约金人民币20000元；乙方离职后未提前三十日书面通知甲方的，另行支付违约金5000元。 |

- A1 后日期区间 2025-06-01→2028-05-31 推算为 36 个月，3 个月试用期未超 6 个月上限，M5 的 `probation_exceed` 误报已消失。
- other_observations：2 条，均为「未低于 80%」「已约定社保」的排除性记录。

### C-004（免责话术）

| risk_type | source | excerpt |
|---|---|---|
| disguised_waiver | llm | 乙方自愿放弃缴纳社会保险，甲方每月补贴乙方300元社保补贴，由此产生的一切后果由乙方自行承担。 |
| disguised_waiver | llm | 乙方在工作期间发生任何伤亡事故，概与甲方无关，甲方不承担任何赔偿责任，乙方及家属不得向甲方主张任何权利。 |
| disguised_waiver | llm | 因客户需求乙方自愿延长工作时间的，不另行支付加班费。 |

- A3 后同一 `risk_type` 的三条 LLM 条目全部保留，M5 被折叠的第 5/6 条已回到报告。
- `social_security_missing` 仍未报（「社会保险」关键词在场）。
- other_observations：1 条，工作地点由甲方单方安排。

### C-005（期限声明与日期区间矛盾）

| 文件 | risk_type | source | excerpt |
|---|---|---|---|
| txt | term_inconsistent | rules | （规则生成，无 excerpt） |
| txt | wage_below_floor | llm | 第七条 劳动报酬：月工资7500元，试用期工资7500元。 |
| docx | term_inconsistent | rules | （规则生成，无 excerpt） |

- `term_inconsistent` **没有消失**，也不应消失：声明 12 个月，日期区间 2025-05-20→2028-05-19 经 A1 推算为 36 个月，二者确实矛盾，GT 要求报出。
- txt 额外出现 `wage_below_floor`：LLM 的 explanation 自述「试用期工资与约定工资相同，未低于 80%，但需结合当地最低工资标准核实」，仍放进 `risks`。这是与 C-001 同类的 LLM 自相矛盾误报，GT 未约束该 type，但值得记录。
- 试用期 1 个月，未触发 `probation_exceed`。
- other_observations：txt 1 条（期限矛盾说明）、docx 1 条（工资排除性记录）。

### C-006（社保缺失 + 试用期工资，OCR 式断行）

| risk_type | source | excerpt |
|---|---|---|
| social_security_missing | rules | （规则生成，无 excerpt） |
| wage_below_floor | llm | 试用期间月工资为3200元 |

- A2 后，跨换行的 excerpt「试用期间月工资为3200元」通过校验，M5 的漏报已修复。
- A1 后日期区间 2025-07-01→2027-06-30 推算为 24 个月，与「二年」一致，M5 的额外 `term_inconsistent` 已消失。
- other_observations：1 条，工作地点随项目安排。

## 4. 与 M5 相比

| 项 | M5 | M6 |
|---|---|---|
| 完整命中 | 4 / 8 | 7 / 8 |
| 漏报 | 1（C-006 wage_below_floor） | 0 |
| 误报文件 | C-001 + C-002/C-003/C-006 的规则误报 | C-001 + C-005.txt 的 LLM 误报 |
| 规则侧日期误报 | 4 条 | 0 |
| C-004 disguised_waiver 条数 | 1（合流折叠） | 3（全部保留） |
| C-006 wage_below_floor | 被 verifier 丢弃 | 保留 |

## 5. 验收

- 测试：`99 passed, 1 deselected`（87 既有 + 12 新增，slow 默认跳过）
- stability：`1 passed`，5 次运行的 `risk_type` 集合与 excerpt 完全一致；5 份原始输出已刷新入库
- C-001 仍误报，按纪律记录归因后留待下一轮，本轮未再加 prompt 补丁
