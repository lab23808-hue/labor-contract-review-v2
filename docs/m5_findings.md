# M5 真实数据试跑 Findings

- 试跑日期：2026-11-02
- 范围：6 份合同、8 个文件（C-002 / C-005 另出 .docx）
- 调用方式：`python -m src.cli review <file>`，逐文件单独进程、单独一次 LLM 调用
- LLM 配置：`DEEPSEEK_MODEL=deepseek-flash`，`DEEPSEEK_THINKING=disabled`，`temperature=0`
- 纪律：本轮未改动任何代码；以下内容仅为观察记录，ground truth 只用于对照，未回流到实现

## 1. 总览

| 文件 | risks | other_observations | meta.sources | LLM 状态 |
|---|---:|---:|---|---|
| [C-001.txt](../tests/fixtures/realistic/C-001.txt) | 1 | 1 | rules 0 / llm 1 | 成功 |
| [C-002.txt](../tests/fixtures/realistic/C-002.txt) | 3 | 1 | rules 1 / llm 2 | 成功 |
| [C-002.docx](../tests/fixtures/realistic/C-002.docx) | 3 | 1 | rules 1 / llm 2 | 成功 |
| [C-003.txt](../tests/fixtures/realistic/C-003.txt) | 2 | 0 | rules 1 / llm 1 | 成功 |
| [C-004.txt](../tests/fixtures/realistic/C-004.txt) | 1 | 1 | rules 0 / llm 1 | 成功 |
| [C-005.txt](../tests/fixtures/realistic/C-005.txt) | 1 | 0 | rules 1 / llm 0 | 成功 |
| [C-005.docx](../tests/fixtures/realistic/C-005.docx) | 1 | 0 | rules 1 / llm 0 | 成功 |
| [C-006.txt](../tests/fixtures/realistic/C-006.txt) | 2 | 0 | rules 2 / llm 0 | 成功 |

管道故障：**0**。8 个文件全部完成 cleaning、解析、抽取、规则、LLM、合流，退出码均为 0。

## 2. 逐份摘要

### C-001（干净样本）

risks：

| risk_type | severity | source | excerpt |
|---|---|---|---|
| probation_exceed | high | llm | 其中试用期自2025年3月1日起至2025年8月31日止 |

- LLM 的 explanation 自述「满 3 年试用期上限为 6 个月，此处 6 个月未超限，故不构成风险」，却仍把它放进 `risks`，属于自相矛盾的误报。
- other_observations：1 条，试用期工资 16000 元为约定工资的约 88.9%，未低于 80%。

### C-002（试工期）

txt 与 docx 的 risk 集合一致：

| risk_type | severity | source | excerpt |
|---|---|---|---|
| disguised_waiver | high | llm | 试工期内甲方可随时以不符合要求为由解除本协议，无需支付补偿 |
| probation_exceed | high | llm | 乙方入职后前四个月为试工期 |
| term_inconsistent | medium | rules | （规则生成，无 excerpt） |

- 规则侧的 `term_inconsistent`：声明 12 个月（「合同期限为一年」），日期区间 2025-04-01→2026-03-31 被推算为 11 个月。
- other_observations：试工期工资 7000 元 / 期满 8500 元，约 82.4%，未低于 80%。

### C-003（普通离职违约金）

| risk_type | severity | source | excerpt |
|---|---|---|---|
| illegal_penalty | high | llm | 乙方在合同期内提前离职的，应向甲方支付违约金人民币20000元；乙方离职后未提前三十日书面通知甲方的，另行支付违约金5000元。 |
| probation_exceed | high | rules | 试用期三个月 |

- 规则把日期区间 2025-06-01→2028-05-31 推算为 35 个月，落入「满 1 年不足 3 年→2 个月」档，因而判定 3 个月试用期超限。
- other_observations：无。

### C-004（免责话术）

| risk_type | severity | source | excerpt |
|---|---|---|---|
| disguised_waiver | high | llm | 乙方自愿放弃缴纳社会保险，甲方每月补贴乙方300元社保补贴，由此产生的一切后果由乙方自行承担。 |

stderr 显示 LLM 还提交了两条同类型风险，被合流器按 `risk_type` 去重丢弃：

- 「乙方在工作期间发生任何伤亡事故，概与甲方无关，甲方不承担任何赔偿责任，乙方及家属不得向甲方主张任何权利。」
- 「因客户需求乙方自愿延长工作时间的，不另行支付加班费。」

- 第 4 条含「社会保险」字样，`social_security_missing` 未报，符合预期。
- other_observations：1 条，工作地点由甲方按业务需要安排，未明确具体地点或变更规则。

### C-005（期限声明与日期区间矛盾）

txt 与 docx 输出完全一致：

| risk_type | severity | source | excerpt |
|---|---|---|---|
| term_inconsistent | medium | rules | （规则生成，无 excerpt） |

- 声明 12 个月（「合同期限为一年」），日期区间 2025-05-20→2028-05-19 推算为 36 个月。
- LLM 也报了同类型风险，合流时规则版胜出，LLM 版被丢弃。
- 试用期 1 个月，未触发 `probation_exceed`。other_observations：无。

### C-006（社保缺失 + 试用期工资，OCR 式断行）

| risk_type | severity | source | excerpt |
|---|---|---|---|
| social_security_missing | high | rules | （规则生成，无 excerpt） |
| term_inconsistent | medium | rules | （规则生成，无 excerpt） |

- `term_inconsistent`：声明 24 个月（「合同期限为二年」），日期区间 2025-07-01→2027-06-30 推算为 23 个月。
- LLM 成功识别了试用期工资问题，但提交的 excerpt「试用期间月工资为3200元」在 cleaning 后的文本中因换行不连续，被 verifier 丢弃。stderr 原文：

```
丢弃 LLM risk：excerpt 不在原文中：'试用期间月工资为3200元'
丢弃 LLM risk：excerpt 不在原文中：'本合同期限为二年，自2025年7月1日起至2027年6月30日止'
丢弃 LLM risk：excerpt 不在原文中：'四、劳动保护 甲方提供安全帽等防护用品，乙方应遵守安全操作规程。'
丢弃 LLM observation：excerpt 不在原文中：'工作地点随甲方项目所在地安排'
```

- other_observations：无。cleaning 本身未报错，未造成管道故障。

## 3. 错题清单（对照 ground truth）

| 文件 | 必须报 | 必须不报 | 实际 | 判定 |
|---|---|---|---|---|
| C-001.txt | 无 | 任何 risks | probation_exceed（llm） | **误报** |
| C-002.txt | probation_exceed（试工期=试用期） | — | probation_exceed（llm）+ disguised_waiver + term_inconsistent | **命中**；附 1 条额外误报（term_inconsistent） |
| C-002.docx | probation_exceed（试工期=试用期） | — | 同 C-002.txt | **命中**；附 1 条额外误报（term_inconsistent） |
| C-003.txt | illegal_penalty | probation_exceed（3 个月/3 年合法） | illegal_penalty（llm）+ probation_exceed（rules） | **部分命中**：illegal_penalty 命中，probation_exceed **误报** |
| C-004.txt | disguised_waiver（LLM，至少报 4/5 两条） | social_security_missing（关键词在场） | disguised_waiver 1 条（仅第 4 条）；social_security_missing 未报 | **部分命中**：risk_type 命中但第 5/6 条被合流折叠；必须不报项正确沉默 |
| C-005.txt | term_inconsistent | probation_exceed（从严口径下 1 个月≤2 个月上限） | term_inconsistent（rules） | **命中** |
| C-005.docx | term_inconsistent | probation_exceed（从严口径下 1 个月≤2 个月上限） | term_inconsistent（rules） | **命中** |
| C-006.txt | social_security_missing（rules）+ wage_below_floor（LLM 单源） | — | social_security_missing（rules）+ term_inconsistent（rules）；wage_below_floor **未出现** | **部分命中**：社保命中，wage_below_floor **漏报**；附 1 条额外误报（term_inconsistent） |

统计：

- 完整命中：4 份（C-002.txt、C-002.docx、C-005.txt、C-005.docx）
- 部分命中：3 份（C-003、C-004、C-006）
- 误报（零风险样本被报）：1 份（C-001）
- 漏报：1 项（C-006 的 wage_below_floor）
- 管道故障：0

### 漏报 / 误报归因

| 现象 | 初步归因 |
|---|---|
| C-001 误报 probation_exceed | LLM 结论与自身论证矛盾仍输出 `risks`；verifier 只校验 excerpt 是否逐字存在，不校验结论是否被 excerpt 支持。 |
| C-002 term_inconsistent（额外） | 抽取器的日期区间月差算法 `(年差×12 + 月差)` 忽略了起止日与区间包含关系，12 个月合同被算成 11 个月。 |
| C-003 probation_exceed（误报） | 同一月差算法把 3 年期算成 35 个月，触发「不足 3 年→2 个月上限」档；合同本身合法。 |
| C-004 第 5/6 条未出现在报告 | 不是 LLM 漏报：LLM 已识别并提交，merger 以 `risk_type` 为唯一键，同类型只保留第一条（stderr 有「重复的 LLM 风险，丢弃后者」）。 |
| C-006 wage_below_floor 漏报 | LLM 已识别，但 OCR 式断行使原文为「试用期间月工资为\n3200元」，cleaning 未合并折行，verifier 的逐字子串校验失败后丢弃。 |
| C-006 term_inconsistent（额外） | 同一月差算法把 2 年期算成 23 个月，与声明 24 个月冲突。 |

## 4. 归因聚类（下一步的候选方向）

1. **日期区间月差算法**：C-002、C-003、C-006 共 4 条错误风险都来自 `(年差×12 + 月差)` 未处理起止日/包含关系。影响的是 rules 的 `term_inconsistent` 与 `probation_exceed`，属于抽取器与规则的边界口径问题。
2. **变相试用期叫法**：C-002 的 `probation_exceed` 只有 LLM 报出，抽取器只认「试用期」，不认「试工期」。规则分支在此类文本上结构性沉默。
3. **verifier 逐字校验 vs. OCR 断行**：C-006 的 `wage_below_floor` 被误杀；同一份里另有 3 条 LLM 输出因断行/跨段引用被丢弃。cleaning 目前保留换行，不做折行合并。
4. **merger 单 risk_type 去重**：C-004 的多条款同类风险只呈现第一条，条款级证据丢失；GT 的「至少报 4/5 两条」在报告层无法满足。
5. **LLM 结论自洽性无校验**：C-001 的误报说明 verifier 只验 excerpt 存在性，未验「excerpt 是否支持该 risk_type」。

## 5. 方法与附注

- 每份文件独立执行一次 CLI，一次 LLM 调用；产物为 CLI 打印的完整 JSON。
- C-001 在正式采集前跑过一次连通性验证，两次 LLM 输出不同：验证那次的 risks 为 2 条（probation_exceed + term_inconsistent，且含「2028 年 2 月 29 日不存在」的错误陈述）；正式采集这次为 1 条。本表以正式采集为准。这说明当前 LLM 分支在 temperature=0 下仍存在运行间波动，单次结果不宜当作稳定基线。
- .docx 由同名 .txt 纯文本逐段贴入生成，未设任何样式；C-002 / C-005 的 txt 与 docx 结果在 risk 集合上一致。
- 本轮未改动代码；已有测试与 stability 基线未重跑，代码未变。
