# src/api 与 src/app 职责边界审计报告

- 日期：2026-11-05
- 范围：`src/api/`、`src/app/`（含 `src/cli.py` 对 `app.batch` 的引用）
- 不在范围：`src/domain`、`src/infra`、`src/core`、`src/config` 的具体实现（按你的判断，这些是"具体实现和配置"，本身没问题）
- 结论先行：真正的问题不是"哪个文件写得长"，而是**三处越界 + 两处重复 + 一套命名撞车**，详见第 3 节。

---

## 1. 现状盘点

### 1.1 src/api

| 文件 | 行数 | 实际职责 | 依赖 | 评价 |
|---|---:|---|---|---|
| `app.py` | 83 | 组装 FastAPI：创建仓库、parsers、validator、审查队列、限流器，挂路由 | infra 具体实现 | ✅ 组装根，位置正确；但参数已膨胀到 12 个 |
| `dependencies.py` | 36 | 从 `app.state` 取对象 | app 具体类 | ⚠️ `get_contract_manager` 每次请求 new 一个，其余都是单例 |
| `errors.py` | 58 | 领域异常 → HTTP 状态码 | core / domain 异常 | ✅ 干净，分层正确 |
| `review_app.py` | 46 | 在 `create_app` 外面再包一层默认参数 | api.app + infra | ❌ 纯过渡壳，已无独立入口价值 |
| `routes/contracts.py` | 277 | 合同 HTTP 接口 **+ 上传/批量编排** + 批量响应模型 | app 具体类、infra 限流器 | ❌ 传输层承担用例编排 |
| `routes/review.py` | 70 | 审查结果/报告/stats HTTP | app 队列具体类 | ✅ 基本干净；路径叫 `contract_id`，内部叫 `task_id`，命名泄漏 |
| `review_presenters.py` | 215 | 报告页视图组装 + Jinja 渲染 | 直接读 `TaskRecord` 字段 | ⚠️ 展示层耦合应用层内部数据结构 |
| `templates/report.html` | - | 报告模板 | - | ✅ |

### 1.2 src/app

| 文件 | 行数 | 实际职责 | 依赖 | 评价 |
|---|---:|---|---|---|
| `contract_manager.py` | 223 | 合同上传/保存/解析/产物 **+ 后台任务 `run_parse_task`** | 仓库端口、parsers、审查队列 | ❌ 越界：合同模块反向依赖审查队列 |
| `review_tasks.py` | 548 | ① SQLite 持久化 ② 内存任务表 ③ 队列准入 ④ 线程池执行 ⑤ TTL 清理线程 ⑥ 重启恢复 | sqlite3、线程、执行器、审查引擎 | ❌ 六种职责压在一个文件 |
| `review.py` | 204 | 审查引擎：规则 → LLM → 验证 → 合并 → trace | domain 规则、infra LLM（延迟导入） | ✅ 逻辑内聚；❌ 名字与另外两个 review 撞车 |
| `batch.py` | 195 | CLI 离线批处理：遍历目录 → 解析 → 清洗 → 审查 | 重新实现 parser 选择与管线 | ⚠️ 与 HTTP 批量同名；与主解析管线重复 |
| `trace.py` | 29 | trace 文件写入工具 | 无 | ⚠️ 位置应在 infra |

### 1.3 当前的依赖方向（文本图）

```
api/routes/contracts.py
   ├─→ app.contract_manager.ContractManager ─→ domain.ports
   ├─→ app.contract_manager.run_parse_task ──→ app.review_tasks.ReviewTaskManager
   ├─→ app.review_tasks.ReviewTaskManager ───→ app.review.run_full_review ─→ infra.llm_client
   └─→ infra.rate_limiter.SlidingWindowRateLimiter   ← 路由直接依赖 infra 具体实现

api/routes/review.py ─→ app.review_tasks.ReviewTaskManager
api/review_presenters.py ─→ （直接读 app.review_tasks.TaskRecord 的字段）

app.batch ─→ infra 具体 parser + app.review 管线（另一条独立实现的 parse→clean→review）
cli.py ─→ app.batch
```

问题一眼可见：**api 同时知道合同、解析、队列、限流四件事；app 内部合同与审查互相打结；同一个"解析→清洗→审查"管线有两份实现。**

---

## 2. 应该守住的边界规则

先把"干净"定义成可执行的硬规则，后面所有方案都围绕它：

1. **api 是传输层**：只做参数解析、调用用例、把结果/异常映射成 HTTP。不得直接编排多个应用对象，不得依赖 infra 具体实现（组装根 `app.py` 除外）。
2. **app 是用例层**：可以 import domain/core/infra 的适配器，但**不得 import fastapi/starlette/BackgroundTasks**，也不得关心 HTTP 状态码。
3. **domain 只表达业务**：不得 import api/app/infra。
4. **infra 只做适配**：不得 import api/app。
5. **一个概念只允许一个名字**：review 引擎、review 队列、HTTP 审查接口、HTTP 批量上传、CLI 批处理，五个概念目前有四个"review/batch"重名。
6. **一条管线只有一份实现**：parse → clean → review 的组装只能出现在一个地方。

---

## 3. 问题清单

### P0-1 传输层承担用例编排（routes/contracts.py）

证据：

- 单文件上传：[contracts.py:77-113](../src/api/routes/contracts.py)
  路由函数里依次做了：读字节 → IP 限流 → 抢队列名额 → 上传落盘 → 抢占解析状态 → 注册后台任务 → 返回。
- 批量上传：[contracts.py:136-206](../src/api/routes/contracts.py)
  同一套编排又复制了一遍：限流 → 保存 → 抢名额 → 启动解析 → 注册任务，失败时还要回滚删除合同。

为什么是问题：

- 任何一个编排细节变化（例如加"手动开始审查"、改回滚策略、改准入顺序）都要改 HTTP 层；
- `TestClient` 之外的调用方（CLI、定时任务、未来的服务间调用）无法复用这段流程；
- 批量与单文件的差异（限流计数、回滚方式）只能靠人肉对照，容易再次分叉。

### P0-2 合同模块反向依赖审查队列（contract_manager.py）

证据：[contract_manager.py:190-223](../src/app/contract_manager.py) 的 `run_parse_task`：

- 参数里带 `review_manager`；
- 解析失败调用 `review_manager.fail_reserved(...)`；
- 解析成功读取 cleaned 产物并 `submit_reserved(...)`。

为什么是问题：

- 合同模块（负责存储与解析）不应该知道"审查队列"的存在，依赖方向反了；
- `contract_manager.py` 因此被迫 import `asyncio`，把后台任务/调度语义带进了领域服务；
- 未来任何"解析完成后的动作"（通知、索引、审查）都会继续往这里堆。

### P0-3 review_tasks.py 六合一

证据（[review_tasks.py](../src/app/review_tasks.py)）：

| 职责 | 位置 |
|---|---|
| SQLite 表结构 + 迁移 | `_SCHEMA` / `ReviewTaskStore._migrate`（:29-93） |
| 任务持久化 CRUD | `ReviewTaskStore`（:59-227） |
| 内存任务表 | `ReviewTaskManager._tasks`（:260） |
| 队列准入 | `BoundedSemaphore`（:254） |
| 执行调度 | `ThreadPoolExecutor` + `_run`（:250、:501） |
| TTL 清理 | `_cleanup_loop`（:533） |
| 重启恢复 | `_recover`（:422） |

为什么是问题：

- 想测试持久化要启动线程池和清理线程；想测试准入要碰 SQLite；
- 想换存储（Redis/Postgres）或换调度方式，只能改同一个文件；
- 548 行里同时存在"数据库长什么样"和"并发策略是什么"，任何一处改动风险都被放大。

### P0-4 同一条管线有两份实现，parser 注册也有三份

三段组装 `parse → clean → review` 出现在：

1. 主链路：`contract_manager.execute_parse`（:143）+ `run_parse_task`（:190）+ `review_tasks._run`（:501）；
2. CLI 离线批处理：[batch.py:58-66](../src/app/batch.py)。

parser 与后缀白名单也分散在三处：

- `api/app.py:21-29` 的 `build_parsers()`（txt/docx/pdf/jpg/png）
- `app/batch.py:15` 的 `_SUFFIXES = {".txt", ".docx", ".pdf"}` 与 :26-37 的 `read_parsed`
- `config/settings.py:7` 的 `ALLOWED_EXTENSIONS`（txt/pdf/docx）

为什么是问题：新增一种格式（例如 `.md`）需要同时改三处，漏一处就会出现"能上传但不能解析""CLI 能跑但 HTTP 不认"这类不一致。

### P1-1 review_app.py 是冗余壳

证据：[review_app.py](../src/api/review_app.py) 的 `create_review_app` 现在只是给 `create_app` 填默认参数，生产入口 `src/main.py` 用的也是 `create_app`。只有测试和压测脚本还在引用。

### P1-2 依赖注入生命周期不一致

证据：[dependencies.py:30-36](../src/api/dependencies.py) 每次请求 `ContractManager(...)`，而 `review_manager`、`rate_limiter`、`parse_semaphore` 都是 `app.state` 单例。`ContractManager` 当前无状态所以不出错，但这是"碰巧没问题"，不是设计。

### P1-3 命名撞车

| 名字 | 实际含义 |
|---|---|
| `app/review.py` | 审查引擎（规则 + LLM + 合并） |
| `app/review_tasks.py` | 审查任务队列与状态存储 |
| `api/routes/review.py` | 查询审查结果的 HTTP 接口 |
| `POST /contracts/batch` | HTTP 批量上传 |
| `app/batch.py` | CLI 离线批量审查 |

另外，同一个 id 在 API 层叫 `contract_id`（`routes/review.py`），在应用层叫 `task_id`（`TaskRecord.task_id`），读代码时需要在脑内翻译。

### P1-4 展示层读取应用层内部结构

证据：[review_presenters.py:112-141](../src/api/review_presenters.py) 直接访问 `record.status / record.filename / record.result / record.finished_at`。应用层字段一变，HTML 层跟着碎。

### P2-1 create_app 参数膨胀

证据：[app.py:39-53](../src/api/app.py) 有 12 个构造参数，且部分是 `settings` 的导入期默认值（函数默认参数在 import 时求值，测试不容易在运行期覆盖配置）。

### P2-2 路由内联响应模型

证据：[contracts.py:35-50](../src/api/routes/contracts.py) 的 `BatchItemResponse / BatchSummary / BatchUploadResponse`。不算错，但会让路由文件继续膨胀。

### 2.3 相对干净、不建议动的部分

- `errors.py`：领域异常 → HTTP 的映射集中，分层正确。
- `routes/review.py`：查询类接口，职责单一。
- `review.py` 内部：规则/LLM/验证/合并的编排清晰，只是名字要改。
- `app.py` 作为组装根：位置正确，只需瘦参数。
- `trace.py`：逻辑无问题，只是建议挪到 infra。

---

## 4. 目标形态

### 4.1 目标依赖方向

```
        ┌──────────── api（传输层）────────────┐
        │  routes / schemas / errors / presenters │
        └───────────────┬──────────────────────┘
                        │ 只调用用例
        ┌───────────────▼──────────────────────┐
        │           app（用例编排层）            │
        │ upload_pipeline / review_service /    │
        │ contract_service / review_engine      │
        └───────────────┬──────────────────────┘
                        │ 依赖端口
        ┌───────────────▼──────────────────────┐
        │      domain（规则、模型、端口）         │
        └───────────────▲──────────────────────┘
                        │ 实现端口
        ┌───────────────┴──────────────────────┐
        │            infra（适配器）             │
        │ sqlite_repository / review_store /    │
        │ parsers / llm_client / rate_limiter   │
        └──────────────────────────────────────┘
```

### 4.2 目标文件树

```
api/
  app.py                  # 唯一组装根：创建对象、注入、挂路由
  dependencies.py         # 从 app.state 取单例
  schemas.py              # 所有 Pydantic 请求/响应模型（含批量）
  errors.py               # 领域异常 → HTTP
  routes/
    contracts.py          # 合同资源 HTTP（薄）
    review.py             # 审查结果 HTTP（薄）
  presenters.py           # 视图 DTO + HTML 渲染

app/
  upload_pipeline.py      # 上传用例编排（单文件 + 批量 + 后台解析）
  contract_service.py     # 合同用例（原 ContractManager，去掉 run_parse_task）
  review_service.py       # 审查用例入口（开始/查询/取消/统计）
  review_engine.py        # 审查引擎（原 review.py）
  offline_batch.py        # CLI 离线批处理（原 batch.py）

infra/
  review_store.py         # SQLite 持久化（从 review_tasks.py 拆出）
  ...
```

`review_tasks.py` 中的 `ReviewTaskManager` 拆为 `app/review_queue.py`（准入/线程池/TTL/恢复，依赖 `ReviewStore` 端口），存储实现落 `infra/review_store.py`。

### 4.3 每个文件的单一职责定义

| 文件 | 职责（一句话） | 允许 | 禁止 |
|---|---|---|---|
| `api/app.py` | 组装根 | import 一切具体实现 | 写业务逻辑 |
| `api/dependencies.py` | 取单例 | 读 `app.state` | 构造业务对象 |
| `api/schemas.py` | 请求/响应模型 | pydantic | 业务判断 |
| `api/errors.py` | 异常映射 | 领域异常 | 业务逻辑 |
| `api/routes/*` | HTTP 参数与状态码 | 调用例、映射异常 | 编排多个应用对象、碰队列/解析细节 |
| `api/presenters.py` | 视图 DTO + 模板 | 只读视图 DTO | 直接读队列内部对象 |
| `app/upload_pipeline.py` | 上传用例：限流 → 准入 → 保存 → 解析 → 提交审查 | domain/core/用例依赖 | fastapi、HTTP 状态码 |
| `app/contract_service.py` | 合同 CRUD/解析/产物 | 仓库端口、parser 端口 | 审查队列 |
| `app/review_service.py` | 审查用例入口 | 合同服务、审查队列 | 解析、HTTP |
| `app/review_queue.py` | 审查任务调度 | 存储端口 | 合同解析、HTTP |
| `infra/review_store.py` | 审查任务持久化 | sqlite3 | 并发策略、调度 |
| `app/review_engine.py` | 规则+LLM+合并 | domain、LLM 端口 | 队列、HTTP |
| `app/offline_batch.py` | CLI 离线批处理 | 合同/审查用例 | HTTP |

---

## 5. 迁移方案

原则：**每一步都能单独提交、单独回滚、测试全绿；第一步不改任何行为。**

### Stage 1：止血（不改行为）

1. 删除 `api/review_app.py`
   - 把测试与压测脚本里的 `create_review_app(...)` 改为 `create_app(...)`（参数已经一一对应）。
2. 新增 `app/upload_pipeline.py`
   - 把 `routes/contracts.py` 中单文件与批量的编排搬进来：
     - `ingest(filename, content, ip)` → `Contract`
     - `ingest_batch(files, ip)` → 批量结果
     - `process(contract_id)` → 解析 + 提交审查（原 `run_parse_task`）
     - `process(contract_id, enqueue_review=False)` → 手动重新解析时只解析不审查
   - 路由只保留：取参数 → 调 pipeline → 返回 / 抛异常。
3. 从 `contract_manager.py` 删除 `run_parse_task`，`contract_manager.py` 不再 import `asyncio`，也不再引用审查队列。
4. `dependencies.py` 的 `get_contract_manager` 改为从 `app.state` 取单例（与其它对象一致）。
5. 把批量响应模型挪到 `api/schemas.py`。
6. 后台任务改为 `background_tasks.add_task(pipeline.process, contract_id)`——FastAPI 的调度机制留在传输层，用例不知道它。

**涉及文件**：`api/routes/contracts.py`、`api/app.py`、`api/dependencies.py`、`app/contract_manager.py`、新增 `app/upload_pipeline.py`、`api/schemas.py`；测试里 `run_parse_task` 的导入改为 `app.upload_pipeline`。

**验收**：176 项测试全绿；单文件与批量行为字节级不变。

### Stage 2：拆 review_tasks.py

1. 新增 `infra/review_store.py`：`ReviewTaskStore`、表结构、迁移逻辑原样搬过去。
2. 新增 `app/review_queue.py`：`ReviewTaskManager` 改名为 `ReviewQueue`，只保留准入、线程池、TTL、恢复；通过构造参数接收 `ReviewStore`。
3. `ReviewTaskManager` 这个旧名字可以留一个薄别名，等 Stage 3 再删，减少一次性改动面。
4. `api/routes/review.py` 改为依赖 `app.review_service.ReviewService`，不再直接摸队列。

**验收**：新增 `tests/test_review_store.py`（持久化/迁移/过期），原有并发、恢复、TTL 测试全部照旧通过。

### Stage 3：改名与去重

1. `app/review.py` → `app/review_engine.py`。
2. `app/batch.py` → `app/offline_batch.py`（CLI 专用），并在文件名/文档里明确"离线目录批处理，与 `POST /contracts/batch` 无关"。
3. 统一 parser 注册表：新增 `infra/parsers.py` 的 `build_parsers()`，`api/app.py` 与 `offline_batch` 都从这里取；`ALLOWED_EXTENSIONS` 与注册表做启动一致性检查（只告警，不阻断测试）。
4. 审查任务主键在应用层统一叫 `contract_id`（存储列可保留 `task_id` 并注释，或做一次表重建迁移）。
5. `api/review_presenters.py` 改为接收 `ReviewView` DTO（由 `review_service` 组装），不再直接读 `TaskRecord`。

### Stage 4（可选）：组装根瘦身

- `create_app` 的 12 个参数收成一个 `AppConfig` dataclass（默认值在运行期求值，测试可覆盖）。
- 评估是否需要把 `review_rps / max_inflight` 与 `RequestThrottle` 的构造收进 `review_service`。

### 不做的事

- 不修改任何规则、prompt、verifier、merger 的判定逻辑；
- 不修改 SQLite 合同仓库的表结构与迁移；
- 不引入新的框架或依赖；
- 不为了"分层"把 `domain` 再拆细。

---

## 6. 风险与回滚

| 风险 | 影响 | 缓解 |
|---|---|---|
| 移动 `run_parse_task` 导致循环 import | 启动失败 | 新模块只依赖 `ContractManager` 与队列的公开方法；import 方向单向下沉 |
| 测试大量改 import | 测试大面积红 | Stage 1 只改导入与调用点，不改断言 |
| 行为细节变化（限流计数、批量回滚） | 回归 | Stage 1 前后各跑一次全量测试；批量用例逐条对照 |
| 拆 store 时迁移逻辑遗漏 | 旧库启动失败 | 保留现有迁移函数原样搬迁，并补一个旧库样本测试 |
| 一次性改名过多 | review 困难 | 改名集中在 Stage 3，单独提交 |

回滚策略：每个 Stage 一个 commit；Stage 1 与 Stage 2 不改变外部 API，回滚只需 revert 对应提交。

---

## 7. 验收清单（可直接执行）

```bash
# 1. app 层不得依赖 HTTP 框架
grep -rn "fastapi\|starlette" src/app

# 2. app 层不得依赖 api 层
grep -rn "from api\|import api" src/app

# 3. 路由不得直接依赖 infra 具体实现（组装根除外）
grep -rn "from infra" src/api/routes src/api/dependencies.py

# 4. 路由不得直接编排队列
grep -rn "review_tasks\|ReviewTaskManager\|review_manager\." src/api/routes

# 5. 管线组装只允许出现一处
grep -rn "run_full_review" src/app src/api

# 6. parser 注册表只允许一处
grep -rn "TxtParser()\|DocxParser()" src/app src/api
```

目标结果：1~4 全部为空；5 只命中 `review_queue.py` 或 `review_service.py`；6 只命中 `infra/parsers.py`（以及 composition root）。

---

## 8. 一句话总结

现在的问题不是文件多，而是**责任没有沿调用方向单向流动**：HTTP 在编排用例、合同在指挥审查、队列在管数据库、同一件事有两个名字和两份实现。按 Stage 1 → 2 → 3 收敛后，每个文件只回答一个问题：

- api：怎么把 HTTP 翻译成用例调用；
- app：一次上传/一次审查要做哪些步骤；
- infra：数据存哪里、外部服务怎么调；
- domain：业务规则是什么。
