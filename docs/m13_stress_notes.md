# M13 并发压测 + 限流 + 故障注入笔记

- 日期：2026-11-03
- 机器：Windows 11 10.0.26200，Intel64 Family 6 Model 186，12 logical CPUs
- Python 3.13.12，模型 `deepseek-flash`
- 语料：`tests/corpus_gen/generated` 16 份
- 客户端节流：`rps=8`，`max_inflight=8`（令牌桶 + 信号量）
- 原始数据：`tests/stress/results.json`
- 复现：`.venv\Scripts\python.exe tests\stress\run_stress.py`

## 1. 阶梯表

| workers | 总耗时 s | 吞吐 items/s | p50 s | p95 s | max s | ok | llm_failed | failed | CPU avg/max | total tokens | GT |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---:|---|
| 1 | 22.92 | 0.70 | 1.26 | 1.72 | 4.49 | 16 | 0 | 0 | 50.1 / 74.1% | 16932 | 16/16 |
| 2 | 12.43 | 1.29 | 1.24 | 3.07 | 3.57 | 16 | 0 | 0 | 46.8 / 69.3% | 16857 | 16/16 |
| 4 | 6.93 | 2.31 | 1.45 | 2.28 | 2.92 | 16 | 0 | 0 | 55.6 / 79.4% | 16964 | 16/16 |
| 8 | 4.86 | 3.30 | 2.41 | 3.76 | 3.79 | 16 | 0 | 0 | 54.8 / 74.8% | 16602 | 16/16 |
| 16 | 5.88 | 2.72 | 4.34 | 5.00 | 5.84 | 16 | 0 | 0 | 52.6 / 75.9% | 16847 | 16/16 |
| 32 | 5.26 | 3.04 | 4.59 | 5.17 | 5.22 | 16 | 0 | 0 | 52.7 / 72.2% | 16759 | 16/16 |

CPU 是 psutil 的系统级占用，不是批处理进程单独占用；采样间隔 0.5s。

## 2. 拐点与归因

**拐点在 N=8。** 总耗时从 22.92s（N=1）降到 4.86s（N=8），之后不再改善：
N=16 反而升到 5.88s，N=32 为 5.26s。p50 从 1.24–1.45s（N=1–4）升到
2.41s（N=8）和 4.34–4.59s（N=16–32）。

归因：

1. **不是本地 CPU**：系统 CPU 平均 46.8–55.6%，峰值 69.3–79.4%，12 个逻辑 CPU
   没有饱和；N=8 之后吞吐不再增长时 CPU 也没有继续上升。批处理大部分时间在等
   API。
2. **不是线程切换/GIL**：如果线程开销主导，N=16/32 应该更差且 CPU 升高；实际
   只是 p50 变长、总耗时基本持平，符合排队而不是计算争用。
3. **主要瓶颈是客户端主动节流 + API 等待**：`max_inflight=8` 把在途请求压在
   8 个，`rps=8` 限制启动速率。N>8 的额外线程在信号量上排队，所以总耗时不再
   下降，p50 上升。N=8 正好等于 max_inflight，是当前配置的膝点。
4. **本窗口内服务商没有触发 429/60s 空响应**：六档全部 0 失败、0 重试，说明
   8 并发的主动节流把请求压在了安全区。要观察服务商真实上限，需要单独做
   max_inflight=12/16 的对照实验，不能把当前数据当作线程模型上限。

## 3. token 总账

- 六档每轮 16 份的 total_tokens 在 **16,602–16,964** 之间，约 1,038–1,060
  tokens/份，波动来自模型输出长度。
- 每份报告 `meta.tokens` 带 prompt/completion/total 与 `estimated=false`；
  batch 汇总在 `summary.tokens`。
- chaos 轮 total_tokens=14,682，因为 G-14/G-16 两次 LLM 尝试都失败，usage 为 0；
  其余 14 份正常计费。

## 4. 故障注入结果

`--chaos --chaos-rate 0.25 --workers 4`：

| 指标 | 值 |
|---|---:|
| 处理文件 | 16 |
| ok | 14 |
| llm_failed | 2（G-14、G-16） |
| error（抛异常） | 0 |
| GT 命中 | 16/16 |
| 总耗时 | 10.30s（含退避的 wall time 19.55s） |
| total_tokens | 14,682 |

日志中的重试原因包括 `invalid_json`、`connection`、`timeout`，退避
1.0–1.4s；每份最多 2 次尝试，没有递归放大。G-14、G-16 两次尝试都命中
chaos，最终 `llm.failed`，但规则仍分别报出 `noncompete_no_pay` 和
`social_security_missing`，所以 GT 不受影响，整批没有中断。

### 排查路径（实际演练）

1. 从 chaos batch 的 `batch.item_failed` 事件直接定位两份：G-14、G-16。
2. 打开 `logs/review_batch_<run_id>.log`，看到 `llm.retry` 的 reason 序列
   和最终的 `llm.failed`（G-14 最后一次是 `timeout`，G-16 是 `invalid_json`）。
3. 打开 `trace_<run_id>/14_G-14/05_llm_raw.json` / `06_llm_verified.json`：
   `llm_failed=true`，`error=timeout`，usage 全 0；`08_final_report.json` 的
   `meta.llm_failed=true` 但仍有 `noncompete_no_pay`（rules）。
4. 同样的路径检查 G-16，得到 `social_security_missing`（rules）。
5. 结论：故障发生在 LLM 分支，不在解析/规则/合流；批处理捕获了失败项，
   规则侧兜底成功。

已知 logging 限制：`llm.retry` / `llm.failed` 事件本身不带文件名（多线程
交织），需要靠 `batch.item_failed` 或 per-item trace 反查。若 M14 要做实时
按文件归因，需要给日志加线程本地的 item context。

## 5. 验收对照

- 六档阶梯：完成，数据见上表与 `tests/stress/results.json`。
- 两种限流故障路径：
  - 429：`tests/test_llm_resilience.py::test_429_triggers_retry_then_failure`
  - 60s 空响应：`test_empty_response_triggers_retry_then_failure`
  - 另有 timeout、chaos 注入、usage 透传用例。
- chaos 批跑不崩：16 份全部处理，2 份 llm_failed，0 份异常中断，GT 16/16。
- token 真实调用非零：阶梯每轮约 1.6–1.7 万 tokens。
- 判定一致：
  - FakeLLM：`test_batch_matches_single_runs_and_sums_zero_tokens` 逐字节对比
    批量与单跑报告。
  - 真实跑：六档与 chaos 的 `gt_match` 均为 16/16。
- 测试：**156 passed, 1 deselected**（既有 143 + 本轮新增 13）。

## 6. M14 方向建议

当前同步线程模型在 `max_inflight=8` 处到顶，且 CPU 没有饱和；这不是 asyncio
能自动解决的问题。建议 M14 先做**受控的 max_inflight 扫描**（8/12/16），
保持 16 份语料和相同 rps 上限，观察：

- 服务商是否开始返回 429、60s 空响应或明显变慢；
- 吞吐是否还能线性增长；
- 如果 max_inflight 提到 16 仍稳定，再评估 asyncio 是否值得；
- 如果服务商先降级，换 asyncio 只会更快撞墙，应优先考虑配额/多 key/请求合并。

在拿到这组数据前，不建议重写为 asyncio。
