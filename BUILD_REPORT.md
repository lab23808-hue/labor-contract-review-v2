# BUILD_REPORT — 合同上传与元数据持久化（M1 + M2）

## 运行方式

```bash
.venv/Scripts/python.exe -m uvicorn main:app --app-dir src --port 8000
```

数据目录由 `DATA_DIR` 指定（默认相对路径 `data/`），布局：

```
data/
├── metadata.db            # SQLite，唯一事实来源
└── files/
    └── <sha256 前 12 位>/
        └── <原文件名>
```

接口：`POST /contracts`（201/409/422）、`GET /contracts/{contract_id}`（200/404）、`GET /contracts?limit=&offset=`（默认 limit 20，上限 100）。测试：`.venv/Scripts/python.exe -m pytest -q`（26 passed）。

## 分层

- `domain/ports.py`：`ContractRepository` 协议（save/get/exists_by_sha256/list）+ 端口错误，不 import api/app/infra
- `infra/sqlite_repository.py`：SQLite 适配器（标准库 `sqlite3`，无新增依赖），实现落盘布局与去重
- `app/contract_manager.py`：用例 `upload/save/get/list/exists_by_sha256`
- `api/`：路由零 try/except，异常统一映射在 `api/errors.py`；组合根在 `api/dependencies.py`
- `api/app.py` 通过 `create_app(repository=...)` 注入适配器，默认使用 SQLite

## 与规格文档的偏离及理由

### 遗留自 M1

1. **Python 3.13.12 代替 3.12**：本机无 3.12，经确认沿用现有 `.venv`；代码未使用 3.12 独有特性。
2. **新增 `ContractNotFoundError`**：规格要求 GET 不存在返回 404 且异常映射收敛，未指定异常类型；用领域错误 + 全局 handler 实现。
3. **`create_app()` 工厂**：规格未规定 app.py 形态；工厂便于测试注入临时目录，避免测试触碰真实 `data/`。
4. **新增 `pyproject.toml`**：声明依赖并配置 pytest `pythonpath = ["src"]`。
5. **错误信息使用中文**：规格只要求含具体原因，未规定语言。

### M2 新增

6. **`UploadService` → `ContractManager`，`UploadSink` → `ContractRepository`**
   M2 要求用例提供 `get/list/exists_by_sha256/save`，端口增加 `list`。保留 M1 的 `upload()` 作为完整上传流程入口，`save()` 委托仓库持久化已构造的 Contract。

7. **`file_format` 列不加 CHECK 白名单**
   白名单由 `ALLOWED_EXTENSIONS` 环境变量驱动；若在 DB 写死 CHECK，用户改配置后合法上传会触发约束错误。`status` 列用 `CHECK (status IN ('uploaded'))`，配合 `PRAGMA user_version = 1` 作为后续迁移留口。

8. **写入顺序：先插入元数据，再写内容文件**
   规格未规定顺序。先 INSERT 使 sha256 主键冲突尽早返回 409；内容写入失败时回滚元数据行，避免留下指向不存在内容的记录。

9. **测试规模整理为 26 条**
   M1 原有 24 条，M2 新增用例后按铁律 20~30 预算，将同类断言合并（校验正反例、大小边界、大小写归一化），覆盖项未减少；新增 SQLite 持久化、缺失文件告警、重启、分页、非法分页参数用例。

10. **`DATA_DIR` 默认 `"data"`（相对路径）**
    沿用 M1 偏离 5 的"不硬编码绝对路径"做法；测试一律传 `tmp_path`，不碰真实 `data/`。

## 已知问题清单

| # | 状态 | 说明 |
|---|------|------|
| M1-1 | **已清偿** | 并发查重：sha256 为主键，`INSERT` 冲突捕获 → `DuplicateContractError` → 409；service 预检查仅用于快速失败 |
| M1-2 | 未变 | 编码探测延后至 M3；txt 仍跳过魔数与编码检查 |
| M1-3 | **已清偿** | SQLite 持久化取代进程内索引；重启后 get/exists_by_sha256/list 均正常（已实测） |
| M1-4 | **已清偿** | 启动不扫描磁盘，DB 是唯一事实来源 |
| M1-5 | **已清偿** | 新增 `GET /contracts` 分页列表（默认 20，上限 100） |
| M1-6 | 未变 | Starlette 对 httpx TestClient 的弃用告警；依赖集合按规格锁定 |

M2 新发现问题：

1. **目录名前 12 位 sha256 碰撞**：48 bit 前缀，概率极低；若碰撞，不同内容会共享目录（文件名不同则并存，文件名相同则后写覆盖）。M2 规格已拍板该布局。
2. **元数据与文件写入非跨资源事务**：进程在 INSERT 提交后、文件写完前崩溃，会留下"有元数据无文件"的记录；`get` 会返回 404 并告警。反向（有文件无元数据）不会发生，除非回滚删除本身失败。
3. **分页基于 `created_at` 偏移**：翻页期间新增合同可能导致重复或漏项；需要稳定游标时再引入。
4. **SQLite 并发写**：单文件 + `timeout=5` 的 busy 等待；极端并发下可能出现 `OperationalError`，当前未映射为 HTTP 状态码（500）。
5. **路径安全依赖写入时校验**：文件路径由原文件名拼接；当前无更新/导入接口，若未来允许直接写 DB，需要重新校验 filename。
6. **无内容下载接口**：本里程碑仅返回元数据，落盘字节只经端口写入。
