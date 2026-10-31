# BUILD_REPORT — 合同审查项目（M1 上传 / M2 持久化 / M3 解析）

## 运行方式

```bash
.venv/Scripts/python.exe -m uvicorn main:app --app-dir src --port 8000
```

PDF/JPG/PNG 解析依赖 MinerU 4.x 的本地服务（MinerU 4 是 client/server 架构）：

```bash
.venv/Scripts/mineru.exe server start      # 启动本地 doclib 服务
.venv/Scripts/mineru.exe server status     # 查看状态（含 managed parse-server）
.venv/Scripts/mineru.exe server stop       # 停止
```

本机已完成：basic 档模型下载、`parse_server.local.mode=managed`、`managed_tier=basic`。服务按需启动——解析 PDF/JPG/PNG 前先 `mineru server start`；txt/docx 不依赖该服务（详见 [README.md](README.md)）。

数据目录由 `DATA_DIR` 指定（默认相对路径 `data/`）：

```
data/
├── metadata.db                                   # SQLite，唯一事实来源
└── files/
    └── <完整 64 位 sha256>/
        ├── <原文件名>                             # 上传的原始字节
        └── artifacts/
            ├── parsed.md                          # 解析产物
            └── cleaned.txt                        # 清洗产物
```

接口：`POST /contracts`、`GET /contracts/{id}`、`GET /contracts?limit=&offset=`、`POST /contracts/{id}/parse`、`GET /contracts/{id}/parsed`、`GET /contracts/{id}/cleaned`。
测试：`.venv/Scripts/python.exe -m pytest -q` → **30 passed**。

## 依赖与模型（M3）

| 依赖 | 版本 | 说明 |
|------|------|------|
| mineru | 4.1.3 | PDF/JPG/PNG 解析（CLI + 本地 doclib 服务） |
| python-docx | 1.2.0 | docx 解析 |
| （MinerU 传递依赖） | onnxruntime-directml 1.24.4、modelscope 1.40.2、gradio 6.31.0 等 | Python 3.13 全部使用轮子，无需编译 |

安装命令与耗时：

```bash
.venv/Scripts/python.exe -m pip install mineru python-docx
```

- 安装耗时约 20 分钟（大量依赖下载），未遇到 Python 3.13 兼容问题，未降级 Python。
- 模型下载：`mineru-kit models download --tier basic --source modelscope`
  - basic 档 13 个文件；主模型 591MB，下载约 3 分 23 秒（约 3 MB/s）；`~/.mineru` 总计约 819MB。
  - 配置：`parse_server.local.mode=managed`、`parse_server.local.managed_tier=basic`。
- 首次真实解析（1 页文本 PDF）：约 24 秒（含模型加载，ONNX DirectML，RTX 3050）后续更快。
- 假 PDF（`%PDF-` 头 + 非法内容）：约 6 秒失败，错误为 `PDFium: Data format error`。

## M3.5 解析链路加固（行为不变）

- **MinerU 子进程超时**：`subprocess.run(..., timeout=660)`（略大于 `--wait 600`），`TimeoutExpired` 转为 `RuntimeError` 后由适配器统一包装为 `ParsingError`。
- **领域错误**：`domain/ports.py` 新增 `ParsingError` 与 `UnsupportedFormatError(ParsingError)`；`ContractManager` 不再抛 `ValueError`。
- **异常收窄**：`ContractManager.parse()` 只捕获 `ParsingError`；`remove_artifacts`、写 `failed` 状态、写 `parsed` 状态均在 `try` 之外，仓储自身出错（如数据库写失败）直接抛给 FastAPI 返回 500，不会伪装成 `failed`。
- **适配器统一包装**：`mineru_parser`、`docx_parser`、`txt_parser` 退出前把非 `ParsingError` 异常包装为 `ParsingError`。
- **启动回收**：`reset_parsing_to_failed()` 在 FastAPI lifespan 启动时执行，把残留 `parsing` 记录改为 `failed`（error=`服务重启导致解析中断`）并返回影响行数。
- **顺带**：`SQLiteContractRepository` 的"不支持的数据库版本"由 `RuntimeError` 改为 `UnsupportedSchemaVersionError`，使 `src/infra` 中的 `RuntimeError` 只出现在解析超时的包装路径。
- **测试调整**：仅同步异常类型断言（`RuntimeError` → `ParsingError`），其余用例与断言未改；30 passed。

## M3.6a 产物下载接口与去重提示

- **`GET /contracts/{id}/parsed`**：返回 `text/markdown; charset=utf-8`；`GET /contracts/{id}/cleaned`：返回 `text/plain; charset=utf-8`；两者都带 `Content-Disposition: attachment; filename="artifact"; filename*=UTF-8''<原文件名>`。
- **未解析合同选择 409**：合同资源存在、只是状态未到 `parsed`（`parsed_path` 为 null）。用 404 会让客户端误以为接口/合同不存在；409 表示"与当前状态冲突，解析完成后再试"，并复用已有的 `InvalidStateError` 映射。`uploaded/parsing/failed` 一律 409。
- **路径安全**：接口不接受任何路径参数；`repository.read_artifact(relative_path)` 只读取 DB 中的相对路径，`resolve()` 后必须仍位于 `DATA_DIR` 内，越界直接报错。
- **文件缺失**：DB 有记录但磁盘文件不存在 → `ArtifactMissingError` → 全局映射为 **500**，detail 含 `artifacts 丢失`。
- **去重提示**：新增 `ContractRepository.find_by_sha256()`；service 预检查与 save 的 `INSERT` 冲突兜底都返回 `文件已存在：contract_id=<已有 id>，sha256=<sha>`。
- 新增测试断言并入现有用例（下载成功/409/500、重复提示含 contract_id），测试总数仍为 30。

## 分层

- `domain/`：`ports.py`（`ContractRepository`、`DocumentParser`、`StoredArtifacts`）、`validation.py`、`cleaning.py`（纯函数清洗器）；不 import api/app/infra
- `infra/`：`sqlite_repository.py`（存储、迁移、artifacts 落盘）、`txt_parser.py`、`docx_parser.py`、`mineru_parser.py`
- `app/contract_manager.py`：`upload/save/get/list/exists_by_sha256/parse`
- `api/`：路由零 try/except，`api/errors.py` 统一映射 404/409/422；组合根在 `api/dependencies.py` 与 `create_app()`

## 与规格文档的偏离及理由

### 遗留自 M1

1. **Python 3.13.12 代替 3.12**：本机无 3.12，经确认沿用现有 `.venv`。
2. **新增 `ContractNotFoundError`**：规格要求 404 且异常映射收敛，未指定异常类型。
3. **`create_app()` 工厂**：便于测试注入临时目录，避免测试触碰真实 `data/`。
4. **新增 `pyproject.toml`**：声明依赖并配置 pytest `pythonpath = ["src"]`。
5. **错误信息使用中文**：规格只要求含具体原因，未规定语言。

### 遗留自 M2

6. **`UploadService` → `ContractManager`，`UploadSink` → `ContractRepository`**：M2 要求用例提供 get/list/exists/save。
7. **`file_format` 不加 CHECK 白名单**：避免与可配置的 `ALLOWED_EXTENSIONS` 冲突；`status` 用 CHECK；`PRAGMA user_version` 做迁移版本。
8. **写入顺序：先元数据后内容**：sha256 主键冲突尽早返回 409；内容写失败回滚元数据行。
9. **测试规模保持在 20~30**：M1/M2 的 26 条合并同类断言，M3 新增用例后总数 30。
10. **`DATA_DIR` 默认 `"data"`**（相对路径），测试一律使用 `tmp_path`。

### M3 新增

11. **MinerU 适配器走官方 CLI，而非 Python API**
    MinerU 4.1.3 的 `parse` 是 doclib 客户端，必须连本地服务。适配器调用 `mineru parse <file> --output <parsed.md> --format markdown --pages all --wait 600`，读取指定输出文件；失败时把 stderr 尾部写入 `error`。**必须传 `--pages all`**，否则默认只解析前 10 页。

12. **清洗规则 ① 与 ④ 的解释**
    ①要求去除 Markdown 语法符号，④要求保留标题层级。cleaned.txt 是纯文本：选择去除 `#/*/**/链接/图片` 等符号，保留正文顺序、条款编号与空行层级间隔；标题层级（`#` 数量）仅保留在 parsed.md。

13. **页眉页脚去除是启发式**
    Markdown 无版面信息，只识别"第 N 页 / N / Page N of M"等页码行、MinerU 注释与 HTML 残留；无法可靠识别任意页眉页脚文本。

14. **目录碰撞修复选择"自动迁移"而非仅告警**
    启动时按 DB 记录把 `files/<sha12>` 重命名为 `files/<sha64>`；新旧目录并存时告警并保留新目录。DB 不存内容路径（由 sha256 + 文件名推导），所以只需改目录；M3 新指针列迁移后为空。旧数据实测可正常读取。

15. **解析失败返回 HTTP 200 + `status=failed`**
    按验收要求"parse → status=failed"；404 仅表示合同不存在，409 仅表示状态冲突（如正在解析）。

16. **JPG/PNG**
    解析路由已映射到 MinerU，但上传白名单默认仍是 `txt,pdf,docx`；需通过 `ALLOWED_EXTENSIONS` 配置放开图片。

17. **状态机**
    `uploaded/failed/parsed → parsing`；`parsing` 再次 parse 返回 409；`parsed` 重新 parse 覆盖 artifacts（幂等）。

18. **artifacts 指针存相对路径**
    DB 中 `parsed_path/cleaned_path` 为相对 `DATA_DIR` 的 POSIX 路径，便于迁移。

## 已知问题清单

| # | 状态 | 说明 |
|---|------|------|
| M1-2 | 部分清偿 | txt 解析按 utf-8 → gb18030 顺序解码并归一化换行（`errors=replace`），但仍不判定"内容是否可读"，也不因编码问题报错 |
| M1-6 | 已消失 | Starlette 对 httpx TestClient 的弃用告警；MinerU 安装带入 httpx2 后不再出现 |
| M2-1 | **已清偿** | 目录碰撞：落盘目录改为完整 64 位 sha256，并自动迁移旧目录 |
| M2-2 | 延续 | 元数据与文件非跨资源事务：M3 失败路径会清理 artifacts；进程在"文件写完、DB 更新前"崩溃会留下无引用 artifacts（下次 parse 覆盖） |
| M2-3 | 延续 | `limit/offset` 分页在并发写入下可能重复/漏项 |
| M2-4 | 延续 | SQLite 并发写有 5s busy 等待，极端并发下可能 `OperationalError`（未映射状态码） |
| M2-5 | 延续 | 路径安全依赖上传时校验；直接改 DB 可绕过 |
| M2-6 | 延续 | 无内容下载接口，只返回元数据与指针 |

M3 新发现问题：

1. **解析同步阻塞耗时**：接口同步等待 MinerU；1 页 PDF 首次约 24 秒（含模型加载），长合同更久，期间占用一个线程池线程。无队列/异步任务（按 M3 规格）。
2. **扫描件质量不承诺**：basic 档 OCR/版面质量未做语料评估；复杂扫描件可能乱码或漏段。
3. **多图不合并**：一个合同对应一个文件；多张图片不会合并为一份合同解析，PDF 内图片也不单独导出，仅输出文本 Markdown。
4. **PDF 依赖本地 MinerU 服务**：doclib 服务未启动时 PDF 解析直接失败（错误信息提示 `mineru server start`）；basic 模型约 819MB，存放于 `~/.mineru`。
5. **`parsing` 状态运行期卡死**：启动时已自动回收（M3.5 的 `reset_parsing_to_failed`）；但进程在解析过程中卡死且不重启时仍会停在 `parsing`（后续 parse 返回 409），留待 M4 异步化时增加运行期超时。
6. **并发 parse 竞态**：两个请求可能都在状态检查后进入解析（结果一致，但会重复调用解析器）。
7. **页码启发式误伤**：独立数字行（如 "1"）会被当作页码删除，可能误删纯数字条款编号。
8. **MinerU 运行时文件**：服务在仓库工作目录生成 `blobs/` 与 `.mineru-service.lock`，已在 `.gitignore` 忽略；真实数据目录 `data/` 亦被忽略。
9. **except 收窄后的异常契约**：解析器新增异常类型必须显式纳入 `ParsingError` 体系；否则会以 500 暴露（有意设计，避免仓储/程序错误被伪装成 `failed`）。
10. **MinerU 本地服务可能被损坏文件打崩**：实测假 PDF 触发 PDFium 异常后 doclib 服务退出；应用侧仍正确返回 `failed`，但需要重新 `mineru server start`。

## 验收记录

- `pytest`：**30 passed**；分层 grep 为空；路由无 try/except。
- docx 实测（HTTP 全链路）：
  - `parsed.md`：`# 劳动合同` / `第一条 试用期三个月。` / `| 甲方 | 乙方 |`
  - `cleaned.txt`：`劳动合同` / `第一条 试用期三个月。` / `| 甲方 | 乙方 |`
- txt 实测：`parse → parsed`，重复 parse 仍 `parsed`；重启进程后 `GET` 仍 `parsed`，`artifacts/` 下 `parsed.md`、`cleaned.txt` 均在。
- 假 PDF 实测：`status=failed`，`error=ParsingError: MinerU 解析失败（...）`，无 artifacts 目录。
- 真实 PDF（MinerU）实测：`status=parsed`，`parsed.md` 含 `<!-- page 1 of 1 -->` 与正文；`cleaned.txt` 已去除注释标记并保留正文。
- 旧 12 位目录迁移：单元测试覆盖（自动重命名 + 旧数据可读）。

## M3.5 验收记录

- grep（限定 `*.py`）：`src/infra` 中 `RuntimeError` 仅剩 MinerU 超时包装一处；`src/app`、`src/domain` 中 `except Exception` 为 0；`ValueError` 为 0。
- 现有 demo 记录 `GET /contracts/21840c04...`：仍为 `parsed`，`parsed_path/cleaned_path/parsed_sha256/cleaned_sha256` 与 M3 时一致。
- 假 PDF：`status=failed`，`error=ParsingError: MinerU 解析失败（...）`，无 artifacts。
- 真实 txt：`status=parsed`，`artifacts/parsed.md`、`cleaned.txt` 正常，内容为「劳动合同 / 第一条 试用期三个月。」
- 模拟中断：手工把记录改为 `status='parsing'` 后重启，`GET` 返回 `status=failed`，`error=服务重启导致解析中断`。
- `pytest`：**30 passed**。

## M3.6a 验收记录

- demo 合同 `GET /parsed`、`GET /cleaned`：HTTP 200，`content-type` 分别为 `text/markdown; charset=utf-8` / `text/plain; charset=utf-8`，中文内容正常，`Content-Disposition` 带 `demo_contract.txt`。
- 未解析的假 PDF：两个下载接口均返回 **409**，detail 为 `合同尚未解析完成，无法下载产物：contract_id=...，status=uploaded`。
- 重复上传：**409**，detail 含已有 `contract_id`。
- 文件缺失场景由测试覆盖：删除 `cleaned.txt` 后下载返回 500，detail 含 `artifacts 丢失`。
