# API 层与 App 层职责划分诊断报告 & 重构方案

> 审查范围：`src/api/` 全目录 + `src/app/` 全目录
> 审查视角：资深 Python 后端工程师 / 六边形架构（Hexagonal Architecture）

---

## 一、现状全景：每个文件现在在干什么

### 1.1 `src/api/` —— Web 接入层

| 文件 | 当前实际职责 | 应有职责 |
|---|---|---|
| `app.py` | FastAPI 创建 + **所有基础设施实例化**（SQLite仓库、5种Parser、Validator、LLMClient、RateLimiter、ReviewTaskManager）+ 路由注册 + Lifespan钩子 | 只负责创建 FastAPI 实例、注册路由和异常处理器；**不应该 new 任何基础设施实现** |
| `review_app.py` | `create_app()` 的薄包装，额外创建一个 `SQLiteContractRepository` 传进去 | 冗余。要么删掉，要么作为唯一的 composition root |
| `dependencies.py` | 从 `app.state` 取依赖；**但 `get_contract_manager` 每次请求 new 一个 ContractManager** | 只做纯透传，不负责业务对象的实例化 |
| `errors.py` | 注册7种业务异常到HTTP状态码的映射 | ✅ 职责清晰，无需改动 |
| `review_presenters.py` | 把审查结果 Record 转成 Jinja2 模板 context（风险类型名映射、严重程度标签、整体判定等） | ✅ 这是 Presenter 模式，职责清晰 |
| `routes/contracts.py` | 路由定义 + **业务编排**（限流检查→reserve名额→upload→start_parse→注册后台任务→失败回滚） | 只做：接参 → 调用例 → 返回。编排逻辑应该下沉到 app 层 |
| `routes/review.py` | 路由定义 + 调 Presenter 渲染页面 | ✅ 基本合格，较薄 |

### 1.2 `src/app/` —— 应用服务层

| 文件 | 当前实际职责 | 应有职责 |
|---|---|---|
| `review.py` | 审查 pipeline 核心：规则执行 → LLM分支 → 结果合并，含trace记录的散点写入 | ✅ 核心业务用例，定位正确；trace是横切关注点，后续可优化 |
| `review_tasks.py` | **三位一体**：① ReviewTaskStore（SQLite持久化，写了完整DDL和CRUD）② ReviewTaskManager（线程池调度、并发信号量、队列 admission、TTL清理线程）③ 任务执行入口（调 run_full_review） | 应该拆成三层：Store（持久化）→ Scheduler（调度）→ Manager（协调） |
| `contract_manager.py` | 合同业务用例（upload/delete/list/get）+ **解析执行细节**（execute_parse 里直接调 parser.parse → clean_text → write_artifacts）+ `run_parse_task`（跨 ContractManager 和 ReviewTaskManager 的衔接编排） | 业务用例层；解析执行应抽成独立的 ParsingService；跨服务编排应放到独立的 use_case 文件 |
| `batch.py` | CLI 批量审查入口：读目录 → 逐个解析 → 调 run_full_review → 汇总结果 | ✅ 这是一个独立的批量用例，定位正确 |
| `trace.py` | TraceRecorder：把中间产物写文件 | ✅ 工具类，职责清晰 |

---

## 二、核心问题诊断（按严重程度排序）

### 🔴 问题1：`api/app.py` 是"上帝工厂"，API层越界做了 Composition Root

**现状**：`create_app()` 函数里直接 `import` 了 `SQLiteContractRepository`、`DeepSeekLLMClient`、`MinerUParser` 等所有 infra 层实现，并在函数体内完成实例化。

**为什么是问题**：
- API 层是**接入层**，它只应该知道"我需要一个 Repository 接口"，不应该知道"用的是 SQLite 还是 PostgreSQL"
- 现在的写法导致：换存储引擎要改 `api/app.py`，换 LLM 客户端也要改 `api/app.py`
- 测试时想 mock 依赖，必须在 `create_app` 的参数列表里传，而这个参数列表已经膨胀到 15+ 个参数

**根因**：缺少一个独立的 **Composition Root**（依赖装配层）。

---

### 🔴 问题2：`routes/contracts.py` 路由层包含完整业务编排

**现状**：`upload_contract` 路由函数里手写了一整套流程：
```
读文件 → IP限流 → 生成uuid → review_manager.reserve() →
manager.upload() → manager.start_parse() →
失败时 release_reservation 回滚 →
background_tasks.add_task(run_parse_task, ...)
```

**为什么是问题**：
- 路由函数应该是"薄"的：接 HTTP 请求 → 调一个用例服务 → 返回响应
- 现在路由里直接操作了 `review_manager.reserve`、`release_reservation` 这些细节，等于把"上传合同并触发审查"这个用例的业务逻辑写在了 HTTP 层
- 以后 CLI 想复用"上传+触发审查"的逻辑？没法复用，因为它绑死在 FastAPI 的 `Request` / `BackgroundTasks` 上

**根因**：缺少 `UploadAndReviewUseCase` 这样的应用服务类。

---

### 🟠 问题3：`app/review_tasks.py` 一个文件塞了三个职责

**现状**：一个文件里有三个核心类：
1. `ReviewTaskStore` —— SQLite 持久化（DDL、CRUD、migration），这是**基础设施层**的活
2. `ReviewTaskManager` —— 线程池调度 + BoundedSemaphore 并发控制 + TTL清理线程，这是**调度层**的活
3. `_run()` 方法里直接调 `run_full_review()` —— 这是**执行层**的活

**为什么是问题**：
- 持久化代码（SQL、PRAGMA、ALTER TABLE migration）出现在 app 层，违反分层——app 层应该只依赖抽象（ports），不应该知道 SQLite
- 调度和持久化耦合在一起：想换存储（比如换成 Redis），要动 Manager 的核心调度逻辑
- 549 行的大文件，改一个地方容易碰坏另一个

---

### 🟠 问题4：`app/contract_manager.py` 里 `execute_parse` 是隐藏的解析流水线

**现状**：`ContractManager.execute_parse()` 里直接写了完整的解析流程：
```python
parser = self._parsers.get(contract.file_format)
parsed_markdown = parser.parse(filename, content)
cleaned_text = clean_text(parsed_markdown)
artifacts = self._repository.write_artifacts(contract, parsed_bytes, cleaned_bytes)
```

**为什么是问题**：
- `ContractManager` 应该是"合同这个聚合的用例入口"，而不是"解析流水线的执行者"
- 解析流水线（parse → clean → write artifacts → 更新状态）是一个独立的能力，应该抽成 `ParsingService`
- 现在 `run_parse_task`（后台任务）、`ContractManager.parse()`（同步调用）、`ContractManager.execute_parse()` 三处都在碰这个流程，逻辑重复风险高

---

### 🟡 问题5：`api/dependencies.py` 里 `get_contract_manager` 偷偷 new 对象

**现状**：
```python
def get_contract_manager(request, repository):
    return ContractManager(repository, request.app.state.parsers, request.app.state.validator)
```

**为什么是问题**：
- DI 函数的约定应该是"从容器里取已有的实例"，而不是"每次请求 new 一个"
- ContractManager 是无状态的（只持有3个引用），完全可以做成单例挂在 app.state 上
- 现在每请求 new 一次，浪费且不符合直觉

---

### 🟡 问题6：`api/review_app.py` 是重复的工厂

**现状**：`create_review_app()` 只是 `create_app()` 传了个 `SQLiteContractRepository` 的默认值。

**为什么是问题**：
- 两个创建入口，新人不知道该用哪个
- `main.py` 用 `create_app()`，测试可能用 `create_review_app()`，行为不一致

---

## 三、重构目标架构

### 分层边界（一句话说清每层是干嘛的）

```
┌─────────────────────────────────────────────────┐
│  api/          → 只关心 HTTP：接请求、转响应、异常转HTTP码    │
│  app/          → 只关心业务用例：编排领域对象完成一个用户需求  │
│  domain/       → 只关心业务规则：纯逻辑，不依赖任何外部        │
│  infra/        → 只关心技术实现：怎么存、怎么调LLM、怎么解析文件 │
└─────────────────────────────────────────────────┘
```

### 重构后目录结构

```
src/
├── api/
│   ├── app.py                  # FastAPI 实例创建 + 路由注册（不再 new 任何 infra）
│   ├── dependencies.py         # 纯透传 DI，不 new 业务对象
│   ├── errors.py               # 异常处理器（不动）
│   ├── container.py            # 【新增】Composition Root：在这里组装所有依赖
│   ├── presenters/            # 【改名】review_presenters → presenters/report.py
│   └── routes/
│       ├── contracts.py        # 薄路由：接参 → 调用例 → 返回
│       └── review.py           # 薄路由（不动）
│
├── app/
│   ├── review.py               # 审查 pipeline 核心（不动）
│   ├── contract_manager.py     # 合同用例（upload/list/delete/get），去掉 execute_parse
│   ├── parsing_service.py      # 【新增】解析流水线：parse → clean → write artifacts
│   ├── review_tasks/           # 【拆包】原来的 review_tasks.py 拆成包
│   │   ├── manager.py          #   ReviewTaskManager：调度 + 协调（不碰SQL）
│   │   └── store.py            #   ReviewTaskStore：持久化（迁到 infra 也行，见下方说明）
│   ├── batch.py                # 批量用例（不动）
│   ├── trace.py                # 工具类（不动）
│   └── usecases/               # 【新增】跨服务编排用例
│       └── upload_and_review.py  # 上传合同 + 触发审查 的完整流程
│
├── infra/
│   ├── sqlite_repository.py     # 合同存储（不动）
│   ├── review_task_sqlite.py    # 【迁移】ReviewTaskStore 从 app 层搬过来
│   ├── llm_client.py            # LLM 客户端（不动）
│   └── ...                     # 其他解析器、限流器（不动）
```

---

## 四、具体改造方案（按优先级，可分步实施）

### 🥇 第一步：把 Composition Root 从 api/app.py 抽出来（解决问题1+6）

**目标**：`api/app.py` 只做"创建 FastAPI 并挂路由"，不 new 任何基础设施。

**做法**：

新建 `src/api/container.py`：
```python
from fastapi import FastAPI
from api.app import create_fastapi_app  # 纯 FastAPI 工厂，不带依赖
from api.dependencies import setup_dependencies
from app.contract_manager import ContractManager
from app.review_tasks import ReviewTaskManager
from infra.sqlite_repository import SQLiteContractRepository
from infra.llm_client import DeepSeekLLMClient
from infra.rate_limiter import SlidingWindowRateLimiter, RequestThrottle
# ... 其他 infra import

def build_container() -> dict:
    """在这里组装所有依赖，返回一个 dict 挂到 app.state 上。"""
    repository = SQLiteContractRepository()
    parsers = build_parsers()
    validator = PdfValidator()
    contract_manager = ContractManager(repository, parsers, validator)
    
    review_client = DeepSeekLLMClient(throttle=RequestThrottle(...))
    review_manager = ReviewTaskManager(review_client, ...)
    
    rate_limiter = SlidingWindowRateLimiter(...)
    parse_semaphore = asyncio.Semaphore(1)
    
    return {
        "contract_repository": repository,
        "contract_manager": contract_manager,
        "review_manager": review_manager,
        "review_rate_limiter": rate_limiter,
        "parse_semaphore": parse_semaphore,
    }
```

改造后的 `api/app.py`：
```python
def create_app() -> FastAPI:
    app = FastAPI(title="Labor Contract Review", lifespan=_lifespan)
    container = build_container()
    app.state.update(container)
    register_exception_handlers(app)
    app.include_router(contracts.router)
    app.include_router(review.router)
    return app
```

**好处**：
- `api/app.py` 从 84 行缩到 ~20 行，一眼看清"这个API有哪些路由"
- 换存储、换 LLM 只改 `container.py` 一个文件
- 测试时可以在 container 层面整体替换 mock，不用塞 15 个参数

---

### 🥈 第二步：把路由里的业务编排下沉到 Use Case（解决问题2）

**目标**：`upload_contract` 路由从 50 行缩到 ~10 行。

**做法**：

新建 `src/app/usecases/upload_and_review.py`：
```python
class UploadAndReviewUseCase:
    def __init__(self, contract_manager, review_manager, rate_limiter, semaphore):
        self.contract_manager = contract_manager
        self.review_manager = review_manager
        self.rate_limiter = rate_limiter
        self.semaphore = semaphore

    def execute(self, filename: str, content: bytes, client_ip: str) -> UploadResult:
        """
        完整流程：限流检查 → reserve → upload → start_parse → 注册后台任务
        返回 contract_id 和状态信息
        """
        # ... 把现在 routes/contracts.py 里的编排逻辑搬过来
```

改造后的路由：
```python
@router.post("/contracts", status_code=201)
async def upload_contract(
    file: UploadFile,
    use_case: Annotated[UploadAndReviewUseCase, Depends(get_upload_use_case)],
):
    content = await file.read()
    result = use_case.execute(file.filename, content, client_ip=request.client.host)
    return result.to_response()
```

**好处**：
- 路由变薄，一眼看清"这个接口调了哪个用例"
- 用例可以被 CLI、其他服务复用，不绑死 HTTP
- 限流、reserve、回滚这些业务规则集中在一处，改起来不会漏

---

### 🥉 第三步：拆分 review_tasks.py（解决问题3）

**目标**：把"调度"和"存储"分开。

**做法**：

1. **把 `ReviewTaskStore` 从 `app/review_tasks.py` 搬到 `infra/review_task_sqlite.py`**
   - 它就是个 SQLite CRUD，属于基础设施
   - 搬到 infra 后，app 层只依赖一个 `ReviewTaskRepository` Protocol（加到 domain/ports.py）

2. **`ReviewTaskManager` 瘦身**：
   - 构造函数接收 `store: ReviewTaskRepository`（接口），不再自己 new SQLite
   - 只保留：线程池调度、并发信号量、队列 admission、TTL 清理
   - `_run()` 方法里调 `run_full_review` 这步保留，因为它是"任务执行"的核心

**改造后的依赖方向**：
```
app/review_tasks/manager.py  → 依赖 →  domain/ports.py (ReviewTaskRepository Protocol)
infra/review_task_sqlite.py  → 实现 →  domain/ports.py (ReviewTaskRepository Protocol)
```

---

### 第四步：抽出 ParsingService（解决问题4）

**目标**：`ContractManager` 只管"合同数据的增删查改"，解析流水线交给专门的服务。

**做法**：

新建 `src/app/parsing_service.py`：
```python
class ParsingService:
    def __init__(self, repository, parsers):
        self.repository = repository
        self.parsers = parsers

    def execute_parse(self, contract_id: str) -> Contract:
        """完整解析流水线：调parser → clean_text → 写产物 → 更新状态"""
        # ... 把 ContractManager.execute_parse 里的逻辑搬过来
```

然后 `ContractManager` 里删掉 `execute_parse`，只保留 `start_parse`（状态抢占）和 `parse`（同步调用 ParsingService）。

`run_parse_task` 后台任务函数也改成调 `ParsingService.execute_parse()`，而不是 `manager.execute_parse()`。

---

### 第五步：清理小问题（解决问题5+6）

1. **删掉 `api/review_app.py`**：它的功能已经被 `container.py` 覆盖了
2. **`dependencies.py` 里 `get_contract_manager` 改成纯透传**：
   ```python
   def get_contract_manager(request: Request) -> ContractManager:
       return request.app.state.contract_manager  # 单例，挂在state上
   ```
   配合第一步的 container，ContractManager 在装配时就建好单例了

---

## 五、改造前后对比（关键文件行数变化预估）

| 文件 | 改造前 | 改造后 | 变化说明 |
|---|---|---|---|
| `api/app.py` | 84行 | ~25行 | 去掉所有 infra import 和实例化 |
| `api/routes/contracts.py` | 278行 | ~120行 | 编排逻辑搬到 use_case，路由变薄 |
| `app/review_tasks.py` | 549行 | ~250行 | Store 搬到 infra，Manager 瘦身 |
| `app/contract_manager.py` | 224行 | ~130行 | execute_parse 搬到 ParsingService |
| **新增** `api/container.py` | — | ~60行 | 集中装配点 |
| **新增** `app/usecases/upload_and_review.py` | — | ~80行 | 上传审查用例 |
| **新增** `app/parsing_service.py` | — | ~70行 | 解析流水线 |

---

## 六、实施建议（小步快跑，每步可回滚）

按照"先抽装配、再抽编排、最后拆大文件"的顺序：

1. **第1步**：抽 container.py，把 create_app 里的实例化搬过去。—— 纯搬家，不改任何业务逻辑，风险最低
2. **第2步**：把 review_app.py 删掉，统一用 create_app。—— 确认 main.py 和测试都走 create_app
3. **第3步**：抽 UploadAndReviewUseCase，把 routes/contracts.py 里的编排搬过去。—— 路由变薄
4. **第4步**：抽 ParsingService，把 execute_parse 搬过去。—— ContractManager 瘦身
5. **第5步**：ReviewTaskStore 搬到 infra，加 Protocol 接口。—— 分层更干净

每一步做完跑一遍现有测试，确认没坏再做下一步。

---

## 七、一句话总结各层的"职责边界"

> **api 层**：我不管你怎么存、怎么算，我只负责"HTTP请求进来 → 调对应的用例 → 把结果转成HTTP响应"。
>
> **app 层**：我不管你底层是 SQLite 还是 MySQL、是 DeepSeek 还是 GPT，我只负责"把各个领域对象按业务流程串起来，完成一个用户需求"。
>
> **domain 层**：我不管谁调我，我只负责"这个业务规则是对的还是错的"。
>
> **infra 层**：我不管你业务上要不要，我只负责"怎么把数据存下来、怎么调外部服务"。
