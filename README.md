# labor_contract_review_v2

劳动合同上传、解析与审查项目（M1 上传 / M2 SQLite 持久化 / M3 解析管道）。

## 运行

```bash
.venv/Scripts/python.exe -m uvicorn main:app --app-dir src --port 8000
```

数据目录由 `DATA_DIR` 指定，默认 `data/`：

```
data/
├── metadata.db
└── files/<64 位 sha256>/
    ├── <原文件名>
    └── artifacts/
        ├── parsed.md
        └── cleaned.txt
```

## MinerU 本地服务（PDF / JPG / PNG 解析前必须启动）

```bash
.venv/Scripts/mineru.exe server start
.venv/Scripts/mineru.exe server stop
```

- **PDF / JPG / PNG** 的解析走 MinerU 4.x 的本地服务，解析前必须先 `server start`。
- **txt / docx** 解析不依赖该服务。
- 首次使用需要准备 basic 档模型并启用 managed 模式（本机已完成）：

```bash
.venv/Scripts/mineru-kit.exe models download --tier basic
.venv/Scripts/mineru.exe config set parse_server.local.managed_tier basic
.venv/Scripts/mineru.exe config set parse_server.local.mode managed
```

- 若损坏文件导致本地服务退出（实测假 PDF 会触发 PDFium 异常），应用会把该合同标为 `failed`；重新执行 `server start` 即可恢复。

## 测试

```bash
.venv/Scripts/python.exe -m pytest -q
```
