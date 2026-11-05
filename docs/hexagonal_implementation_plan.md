# 严格六边形架构实施方案

- 日期：2026-11-05
- 前提：`domain`（端口 + 规则）、`infra`（适配器）是既定的；本次只调整 `api`、`app` 以及 `domain` 中少量反向依赖。
- 底线：**除组装根外，任何层不得直接 import `infra` 的具体实现；跨层只通过 `domain/ports.py` 的端口。**
- 配套背景：`docs/api_app_boundary_report.md`（问题诊断）。本文件只给实施方案，重复内容不赘述。

---

## 1. 目标规则（硬规则，可 grep 验收）

| 层 | 允许 import | 禁止 import |
|---|---|---|
| `core/` | 标准库、pydantic | 其它一切项目内模块 |
| `domain/` | `core`、标准库 | `app`、`api`、`infra`、`config` |
| `app/` | `core`、`domain`（端口/规则）、标准库 | `infra`、`api`、`config`、`sqlite3`、`fastapi` |
| `infra/` | `core`、`domain`（端口）、`config`、标准库 | `app`、`api` |
| `api/` | `app`、`core`、`domain`、`fastapi`、`pydantic` | `infra`（唯一例外：`api/container.py`） |
| 入口（`main.py`、`cli.py`） | 任意 | —（它们本身就是组装根） |
| 测试 | 任意 | —（测试是组装根，允许直接构造 infra） |

> 说明：`api/container.py` 是整棵依赖图的**唯一装配点**。没有它，程序无法启动；它 import infra 不是违规，是职责。

---

## 2. 当前越界清单（全部来自实际扫描）

### 2.1 `domain` → `config`

| 位置 | 引用 | 处理 |
|---|---|---|
| `domain/validation.py:3` | `from config import settings` | 改为接收 `UploadPolicy` 参数 |

### 2.2 `app` → `infra`

| 位置 | 引用 | 处理 |
|---|---|---|
| `app/review.py:202` | 延迟导入 `DeepSeekLLMClient` | 删除默认工厂，`LLMClient` 由外部注入 |
| `app/batch.py:10-11`、`:34` | `DocxParser`、`TxtParser`、`MinerUParser` | 改为构造函数注入 `Mapping[str, DocumentParser]` |

### 2.3 `app` → 存储实现 / 配置

| 位置 | 引用 | 处理 |
|---|---|---|
| `app/review_tasks.py:3,61,64,168` | `sqlite3`、`sqlite3.Row` | Store 迁到 `infra/review_store.py`，app 只依赖 `ReviewTaskRepository` 端口 |
| `app/review_tasks.py:12` | `from config import settings` | 配置由组装根以 `ReviewQueueConfig` 传入 |

### 2.4 `app` → 文件系统（trace）

| 位置 | 引用 | 处理 |
|---|---|---|
| `app/review.py:6` | `app.trace.TraceRecorder`（写文件） | `TraceRecorder` 迁到 `infra/trace.py`；app 依赖 `TraceSink` 端口 |

### 2.5 `api` → `infra`

| 位置 | 引用 | 处理 |
|---|---|---|
| `api/dependencies.py:9` | `SlidingWindowRateLimiter` | 依赖 `RateLimiter` 端口 |
| `api/routes/contracts.py:30` | `SlidingWindowRateLimiter` | 编排下沉后，路由不再接触限流器 |
| `api/review_app.py:10` | `SQLiteContractRepository` | 删除该文件 |
| `api/app.py:12-18` | 全部 infra 实现 | 搬到 `api/container.py`，`app.py` 不再 import infra |

### 2.6 `api` → `app` 具体管理器（编排问题，非 import 方向问题）

| 位置 | 引用 |
|---|---|
| `api/routes/contracts.py:25-26` | `ContractManager`、`run_parse_task`、`ReviewTaskManager` |
| `api/routes/review.py:14` | `ReviewTaskManager` |
| `api/dependencies.py:6-7` | 同上 |

处理：路由只依赖 `UploadPipeline` / `ReviewService` 两个用例入口。

### 2.7 不属于违规、不要动的

- `infra/*` → `domain.ports`、`core`、`config`：适配器依赖端口与配置，正常。
- `cli.py` → `infra/*`：CLI 是入口/组装根，正常。
- `main.py` → `api.app`：入口，正常。
- `tests/*` → `infra/*`：测试是组装根，正常。

---

## 3. 需要新增的端口（`domain/ports.py`）

```python
class LLMClient(Protocol):
    def call_llm(self, prompt: str) -> dict[str, Any]: ...

class RateLimiter(Protocol):
    def check(self, key: str) -> tuple[bool, int, int]: ...

class TraceSink(Protocol):
    def write_json(self, name: str, payload: Any) -> None: ...
    def write_text(self, name: str, text: str) -> None: ...
    def log(self, message: str) -> None: ...

class ReviewTaskRepository(Protocol):
    def create(self, contract_id, filename, cleaned_text, now) -> None: ...
    def set_cleaned_text(self, contract_id, cleaned_text, now) -> None: ...
    def mark_processing(self, contract_id, now) -> None: ...
    def mark_queued(self, contract_id, now) -> None: ...
    def mark_done(self, contract_id, result_json, now) -> None: ...
    def mark_failed(self, contract_id, error, now) -> None: ...
    def delete(self, contract_id) -> None: ...
    def get(self, contract_id) -> ReviewTask | None: ...
    def list_all(self) -> list[ReviewTask]: ...
    def delete_expired(self, cutoff, now) -> list[str]: ...
    def is_expired(self, contract_id) -> bool: ...
    def expired_count(self) -> int: ...
    def close(self) -> None: ...
```

配套新增 `core/review_task.py`：

```python
@dataclass(frozen=True)
class ReviewTask:
    contract_id: str
    filename: str
    status: str
    result: Optional[dict]
    error: Optional[str]
    cleaned_text: Optional[str]
    created_at: float
    updated_at: float
```

> 关键：端口签名里**不能出现 `sqlite3.Row`**。否则 SQLite 类型会顺着接口漏进 app 层，端口等于白加。`infra/review_store.py` 负责 Row → `ReviewTask` 的映射。

---

## 4. 目标文件与职责

```
core/
  contract.py
  review_task.py            # 新增：审查任务实体

domain/
  ports.py                  # 新增 LLMClient / RateLimiter / TraceSink / ReviewTaskRepository
  validation.py             # 去掉 config 依赖，改收 UploadPolicy

app/
  contract_manager.py       # 合同用例（接收 UploadPolicy）
  parsing_service.py        # 新增：抢占 + parse + clean + 写产物
  upload_pipeline.py        # 新增：上传编排（限流/准入/上传/解析/提交审查）
  review_service.py         # 新增：审查用例入口（开始/查询/取消/统计）
  review_queue.py           # 由 review_tasks.py 拆出：调度/准入/TTL/恢复
  review_engine.py          # 由 review.py 改名：规则 + LLM + 合并
  offline_batch.py          # 由 batch.py 改名：CLI 离线批处理（注入 parser/client）

infra/
  review_store.py           # 由 review_tasks.py 的 Store 迁入
  trace.py                  # 由 app/trace.py 迁入
  parsers.py                # 新增：唯一 parser 注册表（txt/docx/pdf/jpg/png）
  （其余不变）

api/
  container.py              # 新增：唯一组装根
  app.py                    # 只创建 FastAPI、挂路由、拿 container
  dependencies.py           # 纯透传，不 import infra
  schemas.py                # 新增：Pydantic 模型（含批量）
  errors.py                 # 不变
  routes/contracts.py       # 薄路由
  routes/review.py          # 薄路由
  presenters.py             # 由 review_presenters.py 改名，接收视图 DTO
```

---

## 5. 分阶段实施

每一步单独提交、单独回滚；每步结束跑全量测试。

### Stage 1：组装根与空壳（不改行为）

1. 新增 `api/container.py`：
   - `AppContainer`（dataclass）：repository / parsers / validator / contract_manager / review_queue / review_service / rate_limiter / parse_semaphore / upload_pipeline。
   - `build_container(**overrides)`：**必须保留 overrides 注入缝**，否则 176 项测试无法注入 fake。
   - 不 import `api.app`，避免循环依赖。
2. `api/app.py`：
   ```python
   def create_app(container: AppContainer | None = None, **overrides) -> FastAPI:
       container = container or build_container(**overrides)
       ...
   ```
   `app.py` 只 import `api.container`、`api.routes`、`api.errors`。
3. 删除 `api/review_app.py`；测试与压测脚本改用 `create_app`。
4. `api/dependencies.py` 改为从 `app.state` 取单例；删除 `infra.rate_limiter` import（类型用端口）。

**验收**：`grep -rn "from infra" src/api` 只命中 `api/container.py`；全量测试绿。

### Stage 2：端口化现有直接依赖

按依赖大小排序，可拆成 2a–2e 多个提交：

| 子步 | 内容 | 影响文件 |
|---|---|---|
| 2a | `RateLimiter` 端口；`SlidingWindowRateLimiter` 实现它 | `domain/ports.py`、`infra/rate_limiter.py` |
| 2b | `LLMClient` 端口移入 domain；删除 `app/review.py` 的 `_default_client` | `domain/ports.py`、`app/review.py`、`infra/llm_client.py` |
| 2c | `TraceSink` 端口；`app/trace.py` → `infra/trace.py`；`run_full_review` 收 sink | `domain/ports.py`、`infra/trace.py`、`app/review.py`、`cli.py`、相关测试 |
| 2d | `UploadPolicy`；`domain/validation.py` 去掉 config；`ContractManager` 接收策略 | `domain/validation.py`、`app/contract_manager.py`、`api/container.py`、相关测试 |
| 2e | `ReviewQueueConfig`；`app/review_tasks.py` 去掉 config 与模块级默认值 | `app/review_tasks.py`、`api/container.py`、测试 |

**验收**：

```bash
grep -rn "from infra\|import infra" src/app src/domain        # 空
grep -rn "from config" src/app src/domain                     # 空
```

`sqlite3` 仍在 `app/review_tasks.py` 中，属于 Stage 3 的存储拆分，完成后才会为空。

### Stage 3：审查任务存储拆分

1. 新增 `infra/review_store.py`：`ReviewTaskStore` 原样迁入，返回 `core.review_task.ReviewTask`。
2. 新增 `app/review_queue.py`：`ReviewTaskManager` 改名 `ReviewQueue`，只保留准入、线程池、TTL、恢复；构造注入 `ReviewTaskRepository`。
3. 新增 `app/review_service.py`：`start(contract_id)` / `get` / `cancel` / `stats`，供路由使用。
4. 路由与依赖改为依赖 `ReviewService`，不再接触队列。

**验收**：端口签名无 `sqlite3.Row`；新增 `tests/test_review_store.py`（持久化/迁移/过期）；新增一个 `FakeReviewTaskRepository` 用于队列单测（`tests/fakes.py`）。

### Stage 4：用例编排与解析服务

1. 新增 `app/parsing_service.py`：把 `ContractManager.execute_parse` 迁入，负责 `claim → parse → clean → artifacts → update`。
2. `ContractManager` 只保留 upload/save/list/get/delete 与产物读取。
3. 新增 `app/upload_pipeline.py`：
   - `ingest(filename, content, ip)`：限流 → 准入 → 上传 → 抢占解析 → 返回 contract
   - `ingest_batch(files, ip)`：逐文件走同一逻辑
   - `process(contract_id)`：解析 → 读取 cleaned → 提交审查
   - `delete(contract_id)`：删除合同 + 取消审查
   - `parse(contract_id, enqueue_review=False)`：手动重新解析
4. 路由只调用 pipeline；后台调度留在路由（`background_tasks.add_task(pipeline.process, contract_id)`），app 层不 import FastAPI。
5. 删除 `run_parse_task`。

**验收**：

```bash
grep -rn "fastapi\|starlette" src/app                 # 空
grep -rn "review_manager\|ReviewTaskManager" src/api/routes   # 空
```

### Stage 5：命名、去重与展示层

1. `app/review.py` → `app/review_engine.py`；`app/batch.py` → `app/offline_batch.py`。
2. 新增 `infra/parsers.py::build_parsers()`，`api/container.py` 与 `offline_batch` 共用；`ALLOWED_EXTENSIONS` 与注册表做启动一致性告警。
3. `offline_batch` 复用 `ParsingService`，删除自带的 `parse → clean → review` 组装。
4. 应用层统一 `contract_id`（存储列可保留 `task_id` 并注释）。
5. `review_presenters.py` → `presenters.py`，接收 `ReviewView` DTO（由 `review_service` 组装），不再读队列内部对象。

---

## 6. 最终验收清单

```bash
# 1. infra 只在组装根出现
grep -rn "from infra\|import infra" src/api | grep -v "api/container.py"
grep -rn "from infra\|import infra" src/app src/domain

# 2. 分层反向依赖
grep -rn "from app\|import app" src/domain src/infra
grep -rn "from api\|import api" src/app src/domain src/infra

# 3. app 不得依赖框架与存储
grep -rn "fastapi\|starlette\|sqlite3" src/app

# 4. 路由不得直接编排队列/限流
grep -rn "ReviewTaskManager\|review_manager\|SlidingWindowRateLimiter" src/api/routes src/api/dependencies.py

# 5. 管线组装只允许一处
grep -rn "run_full_review" src/app src/api

# 6. parser 注册只允许一处
grep -rn "TxtParser()\|DocxParser()\|MinerUParser()" src/app src/api
```

目标：1–4 输出为空；5 只命中 `review_service.py` 或 `upload_pipeline.py`；6 只命中 `infra/parsers.py` 和组装根。

---

## 7. 风险与顺序说明

| 风险 | 缓解 |
|---|---|
| `api/container.py` 反向 import `api.app` 造成循环 | container 只依赖 app/infra/domain，不依赖 api |
| `container` 无注入缝导致测试无法跑 | `build_container(**overrides)` + `create_app(container=None, **overrides)` |
| 端口签名泄漏 `sqlite3.Row` | 端口返回 `core.review_task.ReviewTask`，映射在 infra |
| 一次改太多定位困难 | Stage 1–5 各自独立提交，每步全量测试 |
| `UploadPolicy` 改动波及测试 | Stage 2d 单独提交；为测试保留一个从 settings 构造策略的 helper，但 helper 位于组装根，不在 domain |

顺序理由：先固定装配点（Stage 1），再切断 app/domain 的直接依赖（Stage 2），然后拆存储（Stage 3），最后才动编排与命名（Stage 4–5）。这样每一步都只改一层，回滚粒度清晰。
