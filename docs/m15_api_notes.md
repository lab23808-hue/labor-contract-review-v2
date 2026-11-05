# M15 上传接口与双层有界队列（含 M16 持久化）

> 后续重构说明：`POST /upload` 已移除，上传统一收敛到 `POST /contracts`
> （校验 → 解析 → 清洗 → 审查排队）；本文件保留为 M15 当时的设计记录。

- 日期：2026-11-03
- 新入口：`src/api/review_app.py`（独立于既有合同存储/解析 app）
- 任务层：`src/app/review_tasks.py`（内存队列 + SQLite 事实记录）
- 判定逻辑、prompt、merger、verifier 均未改动；复用 `run_full_review`

## 1. 端点

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/upload` | 校验后立即返回 `task_id`，202；不在请求内同步跑审查 |
| GET | `/result/{task_id}` | 返回 `queued/processing/done/failed` 与结果 JSON；过期任务 410 |
| GET | `/stats` | 队列深度、在途数、累计处理/失败、上限、持久化与 TTL 信息 |

`/upload` 四道检查按序：

1. 扩展名白名单（`.txt` / `.docx` / `.pdf`）且文件 ≤ 2MB，否则 **400**
2. 每 IP 滑动窗口每分钟 ≤ 5 份，否则 **429**；响应带
   `X-RateLimit-Remaining` 与 `Retry-After`
3. 全局有界队列：`queued + in-flight ≤ 50`，否则 **503「系统忙」**
4. 入队并返回 `task_id`

## 2. 处理层

- `ThreadPoolExecutor`，`workers` 默认 8，上限封顶 16。
- 保留 M13 的 `RequestThrottle`（令牌桶 + `max_inflight` 信号量）；
  `max_inflight` 默认 8，上限封顶 16。依据 M14：16 是 p50 翻倍拐点，
  32 只增加吞吐不增加体验。
- 全局准入用 `BoundedSemaphore(50)`：`queued + in-flight` 总数不会超过
  队列上限；队满直接拒绝，不做无限堆积。
- 单份解析/管道异常只把该任务标为 `failed`，不会中断 executor；
  其余任务继续消费。
- 任务状态与结果写入 SQLite（WAL），内存中的队列/线程池结构不变；
  重启时扫库恢复。

## 3. 测试

`tests/test_review_api.py`（M15，5 项）+
`tests/test_task_persistence.py`（M16，5 项）+ 既有套件：

| # | 测试 | 结果 |
|---|---|---|
| 1 | 并发 10 请求同 IP → 5×202、5×429；429 带剩余 0 与 Retry-After | 通过 |
| 2 | 队列填满后再传 → 503，`/stats.total` 仍为 1，拒绝文件未入队 | 通过 |
| 3 | 非法扩展名 / 超过 2MB → 400 | 通过 |
| 4 | 上传后轮询结果与 CLI 单跑 FakeLLM 报告逐字节一致 | 通过 |
| 5 | 混入损坏 docx，其他任务照常完成；stats 为 processed=2/failed=1 | 通过 |
| 6 | done 任务重启后同 task_id 结果逐字节一致 | 通过 |
| 7 | queued 任务重启后恢复并入队完成 | 通过 |
| 8 | 过期 done → 410，内存/任务表删除，墓碑可查 | 通过 |
| 9 | 过期 failed 被清理；queued/processing 不被清理 | 通过 |
| 10 | processing 状态落库后重启 → 最终有确定终态 | 通过 |
| 11 | 既有 161 项 + 新增 5 项 | **166 passed, 1 deselected** |

## 4. 真实并发 `/stats` 验证

命令：`python tests/stress/run_api_stats.py`

配置：真实 DeepSeek，`workers=4`、`max_inflight=4`、`rps=8`、
`queue_limit=50`、`rate_limit=100`，上传 G-01～G-08 共 8 份，
`store_path=":memory:"`（本次只验证并发，不验证重启）。

```
uploads: [202, 202, 202, 202, 202, 202, 202, 202]
peak stats: {"queue_depth": 4, "in_flight": 4, "processed": 0, "failed": 0,
             "total": 8, "queue_limit": 50, "workers": 4, "max_inflight": 4,
             "store": ":memory:", "ttl_seconds": 86400, "expired": 0}
results: {"G-01.txt": [], "G-02.txt": ["probation_exceed"],
          "G-03.txt": [], "G-04.txt": ["illegal_penalty"],
          "G-05.txt": ["noncompete_no_pay"], "G-06.txt": ["term_inconsistent"],
          "G-07.txt": ["wage_below_floor"],
          "G-08.txt": ["social_security_missing"]}
final stats: {"queue_depth": 0, "in_flight": 0, "processed": 8, "failed": 0,
              "total": 8, "queue_limit": 50, "workers": 4, "max_inflight": 4,
              "store": ":memory:", "ttl_seconds": 86400, "expired": 0}
```

峰值 4 在途 + 4 排队；最终 8/8 成功，风险类型与语料 GT 一致。

## 5. M16 持久化与 TTL

### 5.1 SQLite 事实记录

默认文件：`data/review_tasks.sqlite`（WAL 模式，`data/` 已被 gitignore）。

`review_tasks` 表：

```
task_id, filename, status, result_json, error,
content BLOB, created_at, updated_at
```

`content` 是在指令列出的字段之外额外加的一列：queued/processing 任务
重启后要重跑，必须保留原始文件字节；任务进入 done/failed 时立即清空，
不长期保留原文。另有一张 `expired_task_ids(task_id, expired_at)` 墓碑表，
用来区分「从未存在」和「过期被清理」。

状态每次变更都落库：入队 `queued`、开始处理 `processing`、完成 `done`
（写 result_json）、失败 `failed`（写 error）。启动时扫库：

- done/failed：直接加载到内存，`/result` 照常可查；
- queued：重新入队；
- processing：标记为 `queued` 并记录 `task.requeued`，从头重跑。

处理层代码没有改成「队列搬进 SQLite」；SQLite 只是事实记录，恢复靠扫库
重新提交到现有 ThreadPoolExecutor。这个取舍让 M13/M14 的并发结论继续有效。

### 5.2 TTL

- done/failed 默认保留 24 小时（`ttl_seconds` 可配）。
- 后台线程每 10 分钟（`cleanup_interval` 可配）扫一次；只清终态，
  queued/processing 永不淘汰。
- 清理时删除 `review_tasks` 行、同时写 `expired_task_ids` 墓碑、
  从内存 `_tasks` 移除；之后查该 task_id 返回 **410 / 任务已过期**，
  不会和「任务不存在」的 404 混淆。
- processing 重启后从头重跑，不做断点续跑：单份审查几秒，重跑成本远低于
  断点机制复杂度。

### 5.3 真实重启验证

命令：`python tests/stress/run_restart_check.py`

```
before restart: {"task_id": "d639d91e94b3411e80b5e59fc76bd447",
                 "status": "done", "risk_types": ["illegal_penalty"],
                 "tokens": {"prompt_tokens": 969, "completion_tokens": 97,
                            "total_tokens": 1066, "estimated": false}}
after restart:  {"task_id": "d639d91e94b3411e80b5e59fc76bd447",
                 "status": "done", "risk_types": ["illegal_penalty"],
                 "tokens": {"prompt_tokens": 969, "completion_tokens": 97,
                            "total_tokens": 1066, "estimated": false}}
match: True
```

### 5.4 `/stats` 新字段

`store`（SQLite 路径或 `:memory:`）、`ttl_seconds`、`expired`（墓碑数）。
原有 queue_depth / in_flight / processed / failed / total 不变。

## 6. 已知边界

- 任务结果持久化到 SQLite `data/review_tasks.sqlite`（WAL）；服务重启后
  queued/processing 重新入队，done/failed 可查。**不再有「重启即丢失」。**
- done/failed 保留 24h（可配），后台每 10 分钟清理，**不再有「无 TTL」**；
  过期返回 410，任务行删除并留墓碑。
- 已完成的 result 在内存字典中保留到 TTL 清理；24h 内大量任务仍会占内存。
- `content` 只在 queued/processing 阶段保留；done/failed 清空，不长期
  保留上传原文。
- 过期墓碑 `expired_task_ids` 不设 TTL，用来长期回答 410；行很小。
- processing 重启后从头重跑，不能断点续跑；崩溃时最多丢失「正在处理中」
  的进度，已完成任务不受影响。
- **多进程部署仍受限**：IP 限流和内存队列按进程实现；多进程共享 SQLite
  写入、全局限流和跨进程队列需要额外设计（Redis/真正任务队列），本轮不做。
- `/stats` 只反映当前进程内存中的任务，不汇总其他进程。
- SQLite 备份/RPO 未处理；文件本身损坏会影响重启恢复。
- 队列满时返回 503，客户端需要自行重试；没有后台重试队列。

## 7. 启动

```powershell
$env:PYTHONPATH="src"
.venv\Scripts\python.exe -m uvicorn api.review_app:create_review_app --factory --port 8000
```

`create_review_app` 默认构造真实 DeepSeek 客户端，并使用默认 SQLite 文件；
测试通过注入 FakeLLM + `store_path=":memory:"` 隔离。
