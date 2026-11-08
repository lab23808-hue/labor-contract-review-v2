# 劳动合同智能审查系统

> 上传一份劳动合同 → 自动解析切分 → 逐条检索法律依据 → 大模型与规则双轨审查 → 输出可溯源的风险报告

面向律所与法务场景的合同审查工具。系统把「读合同、找法条、判风险、给建议」这条人工流程做成可交互的自动化链路，并保留人工确认环节。

---

## 项目定位

一份劳动合同人工逐条比对劳动法往往需要数十分钟，且容易漏项。本项目解决三个问题：

1. **找法条**：为每条合同条款检索相关法律条文，风险结论必须能追溯到原文。
2. **判风险**：大模型负责开放性判断，确定性规则负责有明确法律标准的判断（如试用期时长、工资比例）。
3. **可复现**：报告固化模型、提示词、规则集、知识库四个版本号，同样的输入得到同样的结果。

---

## 核心能力

| 能力 | 说明 |
|---|---|
| 单一上传入口 | 格式 / 大小 / 文件头校验 → 按 IP 滑动窗口限流 → 有界队列准入 → SHA-256 去重 → 落盘 → 后台解析 → 自动提交审查 |
| 异步解析 | `POST /contracts/{id}/parse` 立即返回 202，解析在后台串行执行，客户端轮询状态 |
| 多格式支持 | txt / docx / PDF / JPG / PNG；损坏 PDF 在上传阶段即被拒绝 |
| 持久化与恢复 | SQLite 记录合同与解析状态，服务重启时回收残留任务 |
| 双轨审查 | 6 条确定性规则 + 大模型审查，结果合并去重 |
| 报告输出 | JSON 结果与 HTML 报告，每条风险附法条依据 |
| 可观测性 | 结构化日志带 run_id，可选链路追踪，`/stats` 查看队列状态 |

---

## 技术栈

- **Web**：FastAPI、Uvicorn、Pydantic v2、Jinja2（HTML 报告）
- **存储**：SQLite（WAL、`PRAGMA user_version` 迁移）
- **解析**：MinerU 4.x（PDF / 图片 OCR，本地服务）、python-docx、pypdf
- **模型**：DeepSeek（OpenAI 兼容接口）
- **测试**：pytest

---

## 架构

```
src/
├── api/        HTTP 层：路由、错误映射、组装根
├── app/        用例层：上传管线、解析服务、审查引擎、任务队列
├── domain/     领域层：字段抽取、确定性规则、结果合并、输出校验
├── infra/      适配器：SQLite 仓储、文档解析器、LLM 客户端、限流器
├── core/       跨层契约：合同、审查任务、领域错误
└── config/     配置
```

依赖方向单向收敛：

```
api  →  app  →  domain / core  ←  infra
                ↑
        api/container.py（唯一组装根）
```

约束由检查脚本与测试强制，而非口头约定：

- `api` 只在 `container.py` 中引用 `infra`
- `app` / `domain` / `core` 不引用 `infra`、`config`
- `domain` / `infra` / `core` 不引用 `app` / `api`
- `app` 不引用 `fastapi` / `starlette` / `sqlite3`
- 路由层不直接接触队列与限流器

---

## 关键设计

### 1. 一条管线，一个事实来源

上传、校验、解析、审查只在一条路径上实现。原始文件只存在于合同仓库，审查只读取清洗后的文本，不保存第二份内容。

### 2. 原子抢占代替「先查后改」

解析请求用单条条件更新抢占：

```sql
UPDATE contracts SET status = 'parsing'
WHERE contract_id = ? AND status IN ('uploaded', 'parsed', 'failed')
```

受影响行数为 1 表示抢到解析权，为 0 则返回 409 或 404。判断与写入合并为一步，避免并发下的重复解析。

### 3. 有界队列与背压

队列满时直接返回 **503**，而不是无限排队。任务状态、限流窗口、准入信号量都在进程内，因此当前设计面向单进程部署。

### 4. 失败可见，不做静默降级

解析或审查失败会落到明确的状态（`failed`）并记录原因，不会伪装成「无风险」。服务启动时会把残留的 `parsing` 记录回收为 `failed`。

### 5. 规则与大模型的分工

试用期时长、试用期工资比例、竞业限制补偿、违约金、社保、合同期限冲突这六类问题有明确法律标准，由规则判断；其余开放性判断交给大模型。两类结果按风险类型合并去重。

---

## 快速开始

```bash
# 安装依赖
pip install -r requirements.txt

# 配置（复制后填入自己的 key）
cp .env.example .env

# 启动服务
uvicorn main:app --app-dir src --port 8000
```

PDF / JPG / PNG 的解析依赖 MinerU 本地服务，解析前需先启动：

```bash
mineru server start
```

txt / docx 不依赖该服务。首次使用需要准备 basic 档模型。

---

## 主要接口

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/contracts` | 唯一上传入口，返回 `contract_id` 并自动进入审查队列 |
| POST | `/contracts/batch` | 批量上传，逐文件走同一管线 |
| GET | `/contracts` | 合同列表（分页） |
| GET | `/contracts/{id}` | 合同详情与状态 |
| POST | `/contracts/{id}/parse` | 异步解析，立即返回 202 |
| GET | `/contracts/{id}/parsed` | 下载解析产物 |
| GET | `/contracts/{id}/cleaned` | 下载清洗后文本 |
| GET | `/result/{contract_id}` | 审查结果（JSON） |
| GET | `/report/{contract_id}` | 审查报告（HTML） |
| GET | `/stats` | 队列运行状态 |
| DELETE | `/contracts/{id}` | 删除合同及其审查任务 |

---

## 测试与压测

```bash
pytest -q
```

当前：**186 passed, 1 deselected**，耗时约 11 秒。

并发压测（16 份缺陷注入合同，见 [docs/m13_stress_notes.md](docs/m13_stress_notes.md)）：

| 并发数 | 总耗时 | 吞吐 | 失败数 |
|---:|---:|---:|---:|
| 1 | 22.92s | 0.70 items/s | 0 |
| 4 | 6.93s | 2.31 items/s | 0 |
| 8 | **4.86s** | **3.30 items/s** | 0 |
| 16 | 5.88s | 2.72 items/s | 0 |

性能拐点在 8 并发（等于 `max_inflight`），继续增加只会拉长 p50 而不提升吞吐。压测同时做了 25% 的 LLM 故障注入，异常被隔离在单任务内。

---

## 已知边界

- **单进程设计**：任务状态、限流计数、队列信号量都在进程内，多 worker 部署需要外置到 Redis 或数据库。
- **无鉴权**：上传、删除、产物下载、报告接口当前全部开放。
- **非 `ParsingError` 的异常**会让合同停留在 `parsing`，只有重启回收才会转为 `failed`。
- **大文件先读入内存再校验**，尚未做流式处理。
- **真实环境未验证**：测试使用 FakeLLM，MinerU 与 DeepSeek 的端到端真实调用尚未纳入自动化测试。

---

## 文档

- [BUILD_REPORT.md](BUILD_REPORT.md) —— 各里程碑的实现记录与偏离说明
- [docs/project_retrospective.md](docs/project_retrospective.md) —— 架构复盘与质量评估
- [docs/m13_stress_notes.md](docs/m13_stress_notes.md) —— 并发压测与故障注入
- [docs/m12_corpus.md](docs/m12_corpus.md) —— 缺陷注入语料与 Ground Truth
