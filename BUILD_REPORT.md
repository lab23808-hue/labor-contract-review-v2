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

本机已完成：basic 档模型下载、`parse_server.local.mode=managed`、`managed_tier=basic`，服务当前处于运行状态（如需停止用上面的 stop 命令）。

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

接口：`POST /contracts`、`GET /contracts/{id}`、`GET /contracts?limit=&offset=`、`POST /contracts/{id}/parse`。
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
5. **`parsing` 状态无超时回收**：解析进程崩溃会永久停在 `parsing`，后续 parse 返回 409，需要手工改库或删除后重传。
6. **并发 parse 竞态**：两个请求可能都在状态检查后进入解析（结果一致，但会重复调用解析器）。
7. **页码启发式误伤**：独立数字行（如 "1"）会被当作页码删除，可能误删纯数字条款编号。
8. **MinerU 运行时文件**：服务在仓库工作目录生成 `blobs/` 与 `.mineru-service.lock`，已在 `.gitignore` 忽略；真实数据目录 `data/` 亦被忽略。

## 验收记录

- `pytest`：**30 passed**；分层 grep 为空；路由无 try/except。
- docx 实测（HTTP 全链路）：
  - `parsed.md`：`# 劳动合同` / `第一条 试用期三个月。` / `| 甲方 | 乙方 |`
  - `cleaned.txt`：`劳动合同` / `第一条 试用期三个月。` / `| 甲方 | 乙方 |`
- txt 实测：`parse → parsed`，重复 parse 仍 `parsed`；重启进程后 `GET` 仍 `parsed`，`artifacts/` 下 `parsed.md`、`cleaned.txt` 均在。
- 假 PDF 实测：`status=failed`，`error=RuntimeError: MinerU 解析失败（exit 1）：错误: Failed to load document (PDFium: Data format error).`，无 artifacts 目录。
- 真实 PDF（MinerU）实测：`status=parsed`，`parsed.md` 含 `<!-- page 1 of 1 -->` 与正文；`cleaned.txt` 已去除注释标记并保留正文。
- 旧 12 位目录迁移：单元测试覆盖（自动重命名 + 旧数据可读）。
