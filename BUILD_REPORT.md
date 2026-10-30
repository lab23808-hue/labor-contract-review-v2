# BUILD_REPORT — M1 合同上传模块

## 运行方式

```bash
.venv/Scripts/python.exe -m uvicorn main:app --app-dir src --port 8000
```

`--app-dir src` 是 src 布局 + 未做包安装时的必需参数。测试：`.venv/Scripts/python.exe -m pytest -q`（24 passed）。

## 与规格文档的偏离及理由

1. **Python 3.13.12 代替 3.12**
   本机只安装了 3.11 和 3.13，没有 3.12。经确认后使用现有 `.venv`（3.13.12）。代码未使用任何 3.12 独有特性，`pyproject.toml` 声明 `requires-python = ">=3.12"`。

2. **新增 `ContractNotFoundError` 与 `UploadService.get()`**
   规格要求 GET 不存在返回 404，且"异常映射全部收敛在 api/errors.py 的全局 handler、路由内零 try/except"，但未指定 404 用哪种异常。实现方式：`core/errors.py` 定义领域错误，`api/errors.py` 统一映射为 404，路由保持无 try/except。

3. **`create_app(sink=None)` 工厂 + `app.state.upload_sink`**
   规格未规定 `api/app.py` 的形态。组合根放在 api 层：默认构造 `TempFileUploadSink`，测试可注入 `TempFileUploadSink(root=tmp_path)`，避免测试写入系统临时目录、也避免进程级单例在测试间互相污染。

4. **新增 `pyproject.toml`**
   规格未列出该文件，也未禁止。用途有两个：声明依赖最小集；配置 pytest 的 `pythonpath = ["src"]`（src 布局未做包安装时的必需项）。

5. **TempFileUploadSink 的根目录**
   规格要求"不硬编码路径"、settings 只有三个环境变量、未给出落盘根配置。实现为 `tempfile.gettempdir() / "labor_contract_review_v2_uploads"`，构造函数允许传入 `root` 供测试隔离。

6. **错误信息使用中文**
   规格只要求"异常信息含具体原因"，未规定语言。合同审查场景面向中文用户，detail 直接透出给调用方。

## 已知问题清单

1. **并发查重最终方案留待 M2**
   当前由 service 层的 `exists_by_sha256` 预检查（快速失败）+ `save()` 内加锁兜底共同消除进程内 check-then-act 竞态。跨进程/多实例场景不在本里程碑解决，M2 可用 sha256 作为落盘文件名实现天然幂等。

2. **编码探测延后至 M3**
   本模块只回答"是不是合法上传文件"：扩展名白名单 + 大小 + 魔数（txt 跳过魔数、不做编码探测）。内容能否解析是 M3 解析阶段的职责。

3. **TempFileUploadSink 的 sha256 索引重启即失**
   索引仅存在于进程内（规格允许）。进程重启后 `exists_by_sha256` 对历史文件恒返回 False，`get()` 也会返回 404。这是临时实现的已知限制，M2 决定正式落盘布局后整体替换。

4. **索引不扫描磁盘**
   启动时不重建索引，只覆盖本进程写入的合同。

5. **无 `GET /contracts` 列表接口**
   按规格明确不做，留待 M2 确定落盘布局后自然实现。

6. **依赖锁定导致测试告警**
   Starlette 1.10 提示 `httpx` TestClient 已弃用、建议改用 httpx2；依赖集合由规格锁定（httpx），本里程碑不调整。
