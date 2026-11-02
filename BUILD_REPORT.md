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

接口：`POST /contracts`、`POST /contracts/batch`、`GET /contracts/{id}`、`GET /contracts?limit=&offset=`、`POST /contracts/{id}/parse`（202，后台执行）、`GET /contracts/{id}/parsed`、`GET /contracts/{id}/cleaned`。
测试：`.venv/Scripts/python.exe -m pytest -q` → **30 passed**。

## 依赖与模型（M3）

| 依赖 | 版本 | 说明 |
|------|------|------|
| mineru | 4.1.3 | PDF/JPG/PNG 解析（CLI + 本地 doclib 服务） |
| python-docx | 1.2.0 | docx 解析 |
| pypdf | 6.21.4 | 上传时 PDF 结构/页数/加密前置校验（原为 MinerU 传递依赖，M3.6b 起显式声明） |
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
- 新增测试断言并入现有用例（下载成功/409/500、重复提示含 contract_id），M3.6a 时测试总数为 30；M3.6b 增加 PDF 内容校验用例后为 31。

## M3.6b PDF 前置校验与 MinerU 崩溃调查

### A. 上传时 PDF 前置校验

- 领域端口新增 `DocumentValidator`，适配器 `infra/pdf_validator.py` 用 **pypdf** 打开 PDF：打不开 / 加密 / 页数为 0 → `CorruptedFileError`（`core/errors.py`）。
- `validate_upload(filename, content, validator)` 增加可注入的校验器参数；`ContractManager.upload()` 传入组合根装配的 `PdfValidator`，domain 不 import pypdf。
- API 全局映射 `CorruptedFileError → 400`，detail 形如 `PDF 内容损坏，无法打开：Stream has ended unexpectedly`。
- 校验器在魔数检查之后执行，假 PDF（`%PDF-` 头 + 损坏内容）在上传阶段即被拒绝，不再进入解析。

### B. MinerU 崩溃机制调查（只调查，未改修复代码）

调查方法：直接调用 `mineru parse` 复现（应用侧已被 A 拦截），失败后立即解析正常 PDF，并检查服务 PID、GPU 显存、日志与锁文件。

| 观测项 | 结果 |
|--------|------|
| 架构 | `mineru_parser.py` 每次请求起一个 `mineru parse` 子进程；该 CLI 是本地 doclib 服务（TCP 15980）的客户端，真正的模型推理在长驻 managed parse-server（`mineru.parser.api_server`，basic 档，端口 16580）中完成 |
| 假 PDF 结果 | CLI exit 1，`PDFium: Data format error`；doclib 记为 `Parse execution failed`，parse-server stderr 打印 traceback |
| 服务状态 | 失败前后 PID 均为 38632（运行时长连续增长），**未退出、未重启** |
| 连坐验证 | 失败后立刻解析正常 PDF：成功，约 28 秒，输出 `<!-- page 1 of 1 -->` |
| GPU 显存 | 基线 886MiB / 6144MiB；正常 PDF 推理时 1924MiB；无 OOM |
| 锁文件 | 仅 `~/.mineru/doclib.lock`（服务启动时间 15:13），失败后未变化，无残留解析锁 |
| 历史"服务消失" | 日志无 shutdown/exit 记录；13:59–14:00 有 managed parse-server 健康检查重启（WinError 10013 后第 3 次成功），doclib 进程在 14:01–15:13 间无日志消失，归因于守护进程生命周期而非 PDF |

**结论**：损坏 PDF 不会打崩 MinerU 服务，只会让当前解析任务失败，应用侧本来就会把合同标为 `failed`。此前的"MinerU 被假 PDF 打崩"是误判。A 项前置校验把这类文件挡在上传阶段；剩余风险是校验器（pypdf）与 MinerU（PDFium）解析能力不完全一致——pypdf 能打开但 PDFium 无法渲染的文件仍可能进入解析并失败（表现为 `failed`，不会影响服务或其他合同）。

## M3.6c 异步解析

- `POST /contracts/{id}/parse`：`ContractManager.start_parse()` 把状态置为 `parsing` 后立即返回 **202 + 合同 JSON**（body 中 `status=parsing`），解析通过 FastAPI `BackgroundTasks` 在响应发出后执行。
- 用例拆分为 `start_parse()`（状态校验/迁移）与 `execute_parse()`（解析→清洗→写 artifacts→更新 DB）；同步的 `parse()` 保留为两者组合，供测试/内部使用。
- 并发控制：`asyncio.Semaphore(1)` 挂在 `app.state`，后台任务 `async with semaphore: await asyncio.to_thread(execute_parse, ...)`。等待信号量时不阻塞事件循环，解析在独立线程中执行；并发请求排队而非并行。该信号量与 app 的事件循环绑定（每个 app 实例一个，生产单进程即全局）；测试多实例天然隔离。
- 状态流转保持 `uploaded → parsing → parsed/failed`；`parsing` 再次提交返回 409；`parsed/failed` 允许重新解析。
- 启动回收保留：重启时 `parsing → failed`（`reset_parsing_to_failed`），已验证强杀进程场景。
- API 响应 JSON 结构不变，仅 `status` 值不同；OpenAPI 的 202 响应描述注明「已受理，轮询 GET /contracts/{id}」，README 同步说明。

## M3.7 删除接口与并发防御

- **`DELETE /contracts/{id}`**：返回 204；不存在 404；`status=parsing` 时 409；其他状态放行。路径仅由账本行的 `sha256` 推导（`DATA_DIR/files/<sha256>/`），不接受客户端路径。
- **失败顺序**：`repository.delete()` 先 `BEGIN IMMEDIATE` 取得写锁 → 校验行/状态 → `shutil.rmtree` 删除整目录 → 删除账本行 → 提交。磁盘不可回滚、DB 可回滚：磁盘删除失败时回滚并保留账行（宁可账上有账、仓库缺房），由 `StorageDeleteError` 映射 500；事务写锁也避免删除与 parse 抢占交错。镜像风险已登记于已知问题 17。
- **上传查重（2a）**：`sha256` 自 v1 起就是 `PRIMARY KEY`（SQLite autoindex，隐含 UNIQUE），INSERT 冲突已转成 `DuplicateContractError` 并在 detail 带已有 `contract_id`；无需新增 UNIQUE 索引或迁移（2c 不适用）。并发双 POST 由数据库唯一约束兜底，恰有一份 201。
- **解析抢占（2b）**：`claim_for_parsing()` 用单条原子 `UPDATE ... SET status='parsing' WHERE contract_id=? AND status IN ('uploaded','parsed','failed')`；受影响 1 行 → 202 + 后台任务，0 行 → 存在则 409、不存在则 404。保留 M3/M3.6c 已验收的 `parsed/failed` 可重解析能力（与字面 SQL 的 `status='uploaded'` 不同，经确认采用此版本）。
- **测试**：新增 `tests/test_delete_contract.py`（4 条）与 `tests/test_concurrency.py`（3 条），总计 **39 passed**；未创建/引用 `test.txt`。
- **受保护记录**：`test.txt`（contract_id `497a8067…`）与其 64 位 sha 目录全程只读，未被迁移或清理逻辑触碰。

## M3.8 批量上传与自动解析

- **`POST /contracts/batch`**：multipart 重复字段 `files`，返回 **200**（整批有成有败，201 只承诺新建成功）。body：`items[]`（filename/contract_id/status/detail）+ `summary`（total/succeeded/failed）。
- **逐文件复用**：直接调用 `ContractManager.upload()`，不复制校验/入库逻辑；每项失败按领域错误转成独立状态码：`ValidationError→422`、`DuplicateContractError→409`（带已有 contract_id）、`CorruptedFileError→400`（与单文件上传一致），成功 `201`。
- **上限**：文件数 ≤ `MAX_BATCH_FILES`（默认 20，读取前检查）、总大小 ≤ `MAX_BATCH_BYTES`（默认 200MB，边读边累计，超限即停），超限整批 422；两个上限均可用环境变量覆盖。
- **自动解析**：201 项在响应前调用 `start_parse()` 原子抢占并 `BackgroundTasks` 排队，detail 标注 `uploaded, parse scheduled`；解析结果通过 `GET /contracts/{id}` 轮询，不进入上传响应。
- **并发**：复用主键去重与原子抢占，不新增并发控制；新增批量 vs 单文件混合竞态测试。
- 新增 `tests/test_batch_upload.py`（4 条），全量 **43 passed**。

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

1. **单个解析耗时与排队**：M3.6c 已改为后台执行，接口立即返回 202；但单个合同解析仍需数十秒（1 页 PDF 首次约 25 秒），且全局并发为 1，多个合同会排队。排队在进程内，非持久任务队列。
2. **扫描件质量不承诺**：basic 档 OCR/版面质量未做语料评估；复杂扫描件可能乱码或漏段。
3. **多图不合并**：一个合同对应一个文件；多张图片不会合并为一份合同解析，PDF 内图片也不单独导出，仅输出文本 Markdown。
4. **PDF 依赖本地 MinerU 服务**：doclib 服务未启动时 PDF 解析直接失败（错误信息提示 `mineru server start`）；basic 模型约 819MB，存放于 `~/.mineru`。
5. **`parsing` 状态运行期卡死**：启动回收覆盖重启场景；若后台任务因非 `ParsingError` 异常中断且进程不重启，状态会停在 `parsing`（后续 parse 返回 409），需要重启或手工处理，留待 M4 持久队列/运行期超时。
6. **并发 parse 竞态**：两个请求可能都在状态检查后进入解析（结果一致，但会重复调用解析器）。
7. **页码启发式误伤**：独立数字行（如 "1"）会被当作页码删除，可能误删纯数字条款编号。
8. **MinerU 运行时文件**：服务在仓库工作目录生成 `blobs/` 与 `.mineru-service.lock`，已在 `.gitignore` 忽略；真实数据目录 `data/` 亦被忽略。
9. **except 收窄后的异常契约**：解析器新增异常类型必须显式纳入 `ParsingError` 体系；否则会以 500 暴露（有意设计，避免仓储/程序错误被伪装成 `failed`）。
10. **MinerU 服务与损坏文件（M3.6b 复核，推翻此前的误判）**：受控实验显示，损坏 PDF 只让该次解析任务失败——doclib 记录 `Parse execution failed`，parse-server 打印 Pdfium traceback，但服务 PID 不变；紧接着解析正常 PDF 成功、无连坐。此前"假 PDF 打崩服务"的判断不成立：日志显示 13:59 发生过 managed parse-server 健康检查重启（伴随 WinError 10013），doclib 进程在 14:01–15:13 之间无日志退出，更像从临时 shell 启动的守护进程生命周期问题。M3.6b 的上传前置校验仍会阻止此类文件进入解析。
11. **managed parse-server 重启可能遇到 WinError 10013**：13:59–14:00 健康检查连续重启时出现"访问权限不允许的套接字尝试"，第 3 次重试成功。MinerU 内部重试可恢复；若持续失败，PDF 解析不可用（与损坏文件无关）。
12. **BackgroundTasks 非持久队列**：进程崩溃时排队/执行中的解析任务丢失；重启后只把状态回收为 `failed`，不会自动重试。
13. **多 worker 部署**：`asyncio.Semaphore(1)` 为进程内信号量；若以 `--workers > 1` 启动，多个进程会并行解析。当前约定单 worker 部署，M4 引入共享队列后再放开。
14. **删除期间持有 SQLite 写锁**：`DELETE` 在 `BEGIN IMMEDIATE` 事务内执行 `rmtree`，期间其他写请求等待（busy timeout 5s）；目录很大时可能超时失败。
15. **删除中途崩溃**：事务回滚会保留账行，但磁盘可能已被部分删除，之后 `GET` 会因原文缺失告警；跨资源删除无法完全原子，属已知取舍。
16. **并发双 parse 的 409 窗口**：仅当第一条仍处于 `parsing` 时第二条才 409；若第一条极快完成，第二条会按“可重解析”返回 202（这是保留重解析能力的既有语义，非竞态）。
17. **DELETE 镜像风险**：DELETE 采用「先删磁盘目录、后删账本行」顺序。若 `rmtree` 成功但随后 SQLite 事务提交失败（本地单机场景概率极低），会出现账上有记录但磁盘目录已删除的账实不符：`GET` 返回该记录，但 `parsed_path` / `files` 路径指向不存在的目录。处置：登记为已知问题，不返工；若未来需要修复，方案为删除后启动时对账（扫描账本行校验目录存在性，缺失则标记 `failed` 或清理账行）。
18. **BackgroundTask 非持久化在批量自动解析下暴露概率上升**：`POST /contracts/batch` 会为每个成功项自动排解析任务，任务只存在于进程内存；服务重启时 pending 任务丢失，账本残留 `parsing` 状态（启动回收会改为 `failed`）。单文件时代概率低，批量时代更常见。处置：登记不返工；修复方向为 Celery + Redis 等持久队列。

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

## M3.6b 验收记录

- 假 PDF（`%PDF-1.7` 头 + 损坏内容）上传 → **400**，detail=`PDF 内容损坏，无法打开：Stream has ended unexpectedly`，列表无新记录。
- 正常 PDF 上传 → **201**，status=`uploaded`。
- 加密 PDF、0 页 PDF 由 `validate_upload` 单元测试覆盖，均抛 `CorruptedFileError`。
- `pytest`：**31 passed**。
- MinerU 调查：损坏 PDF 仅单任务失败、服务 PID 不变、无连坐、无残留锁；详见上节结论。

## M3.6c 验收记录

- txt：`POST /parse` 返回 **202**（0.018s），body `status=parsing`；轮询 `GET` 后为 `parsed`。
- PDF：`POST /parse` 返回 **202**（0.013s）；解析中再次 `POST` → **409**。
- 强杀：解析中 `kill -9` 服务，重启后该合同 `status=failed`，`error=服务重启导致解析中断`。
- 连续提交 3 个 txt：均完成且产物内容各自正确、无交错（A/B/C 三份 cleaned.txt 与原文一致）。
- 并发串行由单元测试 `test_parse_tasks_are_serialized` 覆盖（3 个任务最大并发 = 1）。
- `pytest`：**32 passed**。

## M3.7 验收记录

- DELETE：上传后删除 → **204**，`GET` 变 404，`data/files/<sha256>/` 消失。
- DELETE 不存在 → **404**；`parsing` → **409**（账行与目录保留）；模拟 `rmtree` 抛错 → **500**，账行与目录保留。
- 并发：仓库层双写同 sha 恰一个成功、一个 `DuplicateContractError`（含成功方 contract_id）；API 双 POST → 201+409；API 双 parse（慢解析器占住 parsing）→ 202+409。
- 受保护 `test.txt` 记录与其目录只读核对，状态仍为 `parsed`、目录存在。
- 全量 `pytest`：**39 passed**。

## M3.8 验收记录

- 批量逐项状态：`new.txt→201（parse scheduled）`、`bad.exe→422`、`dup.txt→409（带已有 id）`、`broken.pdf→400`；summary total=4/succeeded=1/failed=3。
- 自动解析：201 项轮询后 `status=parsed`。
- 上限：21 个文件 → 422；缩小 `MAX_BATCH_BYTES` 后超总大小 → 422。
- 并发：批量与单文件同时上传同一内容，恰一方 201、另一方 409，失败方 detail 含成功方 contract_id。
- 全量 `pytest`：**43 passed**。
