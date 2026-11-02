# M12 生成式覆盖语料

- 日期：2026-11-02
- 目录：`tests/corpus_gen/`
  - `template.txt`：合法合同骨架（页眉/页脚 + `{{SECTIONS}}` 槽）
  - `generate.py`：16 个缺陷注入构造器 + 渲染/校验脚本
  - `expected.json`：注入缺陷 → 期望 risk_type 列表
  - `generated/G-01.txt` … `G-16.txt`：生成结果
- 未改动 `src/` 下任何管道代码

## 1. 语料矩阵

| 文件 | 注入缺陷 | expected_risks | 实跑结果 |
|---|---|---|---|
| G-01 | 干净基线 | [] | [] |
| G-02 | 试用期超上限（1 年期 / 4 个月） | probation_exceed | probation_exceed |
| G-03 | 五险缺一（缺生育保险） | []（已知边界） | [] |
| G-04 | 普通离职违约金 | illegal_penalty | illegal_penalty |
| G-05 | 竞业限制无补偿 + 条款外经济补偿（M11 野题） | noncompete_no_pay | noncompete_no_pay |
| G-06 | 声明一年 / 日期区间三年 | term_inconsistent | term_inconsistent |
| G-07 | 试用期工资低于 80%（10000 / 7000） | wage_below_floor | wage_below_floor |
| G-08 | 整条删除社会保险 | social_security_missing | social_security_missing |
| G-09 | 整条删除劳动报酬 | []（已知边界） | [] |
| G-10 | 整条删除合同期限 | []（已知边界） | [] |
| G-11 | G-02 重写：阿拉伯数字 + 条款顺序打乱 | probation_exceed | probation_exceed |
| G-12 | G-04 重写：同义改写，保留「违约金」 | illegal_penalty | illegal_penalty |
| G-13 | G-06 重写：日期前置、声明后置 | term_inconsistent | term_inconsistent |
| G-14 | G-05 重写：同义改写 + 条款外补偿 | noncompete_no_pay | noncompete_no_pay |
| G-15 | G-07 重写：12000 / 9000 | wage_below_floor | **[]（未报）** |
| G-16 | G-08 重写：删除社保 + 条款顺序打乱 | social_security_missing | social_security_missing |

矩阵名义 16 份；指令显式列到 G-12，G-13～G-16 为补齐数量的重写变体，
分别覆盖期限冲突、竞业无补偿、工资下限、社保删除。

## 2. 实跑结论

- 15 / 16 与 GT 一致。
- G-15 在 4 次独立运行中均为 0 risks：LLM 未报 `wage_below_floor`，
  规则侧因抽取器尚无 `probation_salary` 字段而沉默。这是本轮记录的
  LLM 召回缺口，GT 保留期望值，不为过语料修改规则或措辞。
- G-08 由规则抓到 `social_security_missing`；G-09、G-10 如预期没有
  对应 risk_type，GT 留空并记入已知边界。

## 3. 已知边界

1. **五险缺一（G-03）**：确定性规则只检查「社会保险/社保/五险」等
   关键词，不校验五险是否齐全；`expected_risks` 为空。
2. **劳动报酬整条缺失（G-09）**：现有规则没有「报酬缺失」类型，
   `expected_risks` 为空。
3. **合同期限整条缺失（G-10）**：`term_inconsistent` 只处理前后矛盾，
   不处理缺失，`expected_risks` 为空。
4. **工资下限的 LLM 单源（G-07 / G-15）**：规则没有
   `probation_salary` 字段；G-07 本轮命中，G-15 连续 4 次未命中。
   这是模型召回波动，不是规则回归。

## 4. 复现

```bash
python tests/corpus_gen/generate.py
python tests/corpus_gen/generate.py --check
```

`tests/test_corpus_gen.py` 会校验脚本输出、已提交的 generated 文件与
expected.json 三者一致；该测试不调用审查管道。
