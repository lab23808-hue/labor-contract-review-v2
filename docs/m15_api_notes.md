# M15 上传接口与双层有界队列

- 日期：2026-11-03
- 新入口：`src/api/review_app.py`（独立于既有合同存储/解析 app）
- 任务层：`src/app/review_tasks.py`（内存任务队列）
- 判定逻辑、prompt、merger、verifier 均未改动；复用 `run_full_review`

## 1. 端点

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/upload` | 校验后立即返回 `task_id`，202；不在请求内同步跑审查 |
| GET | `/result/{task_id}` | 返回 `queued/processing/done/failed` 与结果 JSON |
| GET | `/stats` | 队列深度、在途数、累计处理/失败、上限与 worker 配置 |

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
- 任务状态与结果只存内存，重启丢失——这是本轮明确的已知边界。

## 3. 测试

`tests/test_review_api.py`（5 项）+ 既有套件：

| # | 测试 | 结果 |
|---|---|---|
| 1 | 并发 10 请求同 IP → 5×202、5×429；429 带剩余 0 与 Retry-After | 通过 |
| 2 | 队列填满后再传 → 503，`/stats.total` 仍为 1，拒绝文件未入队 | 通过 |
| 3 | 非法扩展名 / 超过 2MB → 400 | 通过 |
| 4 | 上传后轮询结果与 CLI 单跑 FakeLLM 报告逐字节一致 | 通过 |
| 5 | 混入损坏 docx，其他任务照常完成；stats 为 processed=2/failed=1 | 通过 |
| 6 | 既有 156 项 + 新增 5 项 | **161 passed, 1 deselected** |

## 4. 真实并发 `/stats` 验证

命令：`python tests/stress/run_api_stats.py`

配置：真实 DeepSeek，`workers=4`、`max_inflight=4`、`rps=8`、
`queue_limit=50`、`rate_limit=100`，上传 G-01～G-08 共 8 份。

```
uploads: [202, 202, 202, 202, 202, 202, 202, 202]
peak stats: {"queue_depth": 4, "in_flight": 4, "processed": 0, "failed": 0,
             "total": 8, "queue_limit": 50, "workers": 4, "max_inflight": 4}
results: {"G-01.txt": [], "G-02.txt": ["probation_exceed"],
          "G-03.txt": [], "G-04.txt": ["illegal_penalty"],
          "G-05.txt": ["noncompete_no_pay"], "G-06.txt": ["term_inconsistent"],
          "G-07.txt": ["wage_below_floor"],
          "G-08.txt": ["social_security_missing"]}
final stats: {"queue_depth": 0, "in_flight": 0, "processed": 8, "failed": 0,
              "total": 8, "queue_limit": 50, "workers": 4, "max_inflight": 4}
```

峰值正好是 4 个在途 + 4 个排队（8 份、4 个 worker），最终 8/8 处理成功，
风险类型与语料 GT 一致。

## 5. 已知边界

- 任务与结果只在内存，**服务重启即丢失**；不做登录、数据库、结果页面。
- 已完成/失败的任务结果保留在内存字典中直到进程重启，没有 TTL 或淘汰。
- IP 限流按进程内存实现；多进程部署时每个进程各自计数，需要共享存储
  才能全局限流。
- `/stats` 只反映当前进程的任务；没有持久化历史。
- 上传文件在任务处理完后即随闭包释放，不保留原文；只保留结果 JSON。
- 队列满时返回 503，客户端需要自行重试；没有后台重试队列。

## 6. 启动

```powershell
$env:PYTHONPATH="src"
.venv\Scripts\python.exe -m uvicorn api.review_app:create_review_app --factory --port 8000
```

`create_review_app` 默认构造真实 DeepSeek 客户端；测试通过注入 FakeLLM
避免外部调用。
