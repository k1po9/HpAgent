# File Assistant F0–F4 实现地图与漂移审计

审计基线：`feat/hpagent-web`，F0–F3 Runtime Closure 工作树。

阶段结论：

- **F0 Runtime Complete**
- **F1 Runtime Complete**
- **F2 Runtime Complete**
- **F3 Runtime Complete**
- **F4 Runtime Complete**

本文用于把产品化指南中的 File Assistant 目标与当前代码逐项对照。状态分为：

- **完成**：生产控制流已有代码、持久化或运行时测试证据。
- **部分完成**：基础契约存在，但还没有完整生产闭环。
- **未实现**：仅有技术预留、依赖或设计说明，不能视为可用能力。

## 1. 当前架构

```text
Web message + attached stored_files
  -> run_files(input)
  -> WorkspaceResourcePreparer / RunFileScope
  -> FileResourceResolver
  -> Tool Registry
       |- bounded read tools -> format adapters
       |- immutable write/patch tools -> outputs/
       `- conversion tool -> Gotenberg
  -> OutputPublisher
  -> TenantFileStore
  -> stored_files(output, parent_file_id, version)
  -> run_files(output, operation_id)
  -> completed assistant message_files
  -> authenticated metadata / lineage / download API

Large structured document
  -> deterministic size/media-type routing from ordinary Agent read tool
  -> NormalizeDocumentWorkflow
  -> dedicated document task queue / low-concurrency Worker
  -> DoclingStructuredDocumentProvider
  -> normalized_documents
  -> compact NormalizedDocumentRef in Temporal History

Future destructive file action
  -> file_action_approvals request
  -> account approve/reject API
  -> exact grant consume
  -> [尚未接入] Temporal resume + non_idempotent_write adapter
  -> existing Operation Intent / fencing / reconciliation
```

## 2. 分阶段实现对照

| 阶段 | 指南目标 | 当前实现 | 状态 |
|---|---|---|---|
| F0 | 文件领域模型和 Provider 边界 | `FileResource`、`SourceLocator`、`NormalizedBlock/Table/Document`、`TextView`；`FastTextViewProvider`、`StructuredDocumentProvider`、`ConversionProvider` | 完成 |
| F0 | Run File Scope，不接受任意宿主路径 | `RunFileScope`、`FileResourceResolver`；只解析当前 Run input 或已发布 output，拒绝逃逸、符号链接和非普通文件 | 完成 |
| F0 | 快速文本查看 | `MarkItDownFastTextViewProvider`；`read_file_text` 提供有界结果 | 完成 |
| F0 | 正式 Web binary upload | PDF/DOCX/XLSX/PPTX 流式写入 `stored_files`，保留 size/SHA256/immutable storage；仅文本执行 UTF-8 校验 | 完成 |
| F0 | PDF / DOCX / XLSX / PPTX 有界读取 | `PdfAdapter`、`DocxAdapter`、`XlsxAdapter`、`PptxAdapter`；页、表、段落、range、slide 均要求显式范围 | 完成 |
| F0 | Tool Registry 与副作用分类 | 文件读工具注册为 `read_only`，需要 file scope，并声明预算字段 | 完成 |
| F1 | Docling 结构化文档 | `DoclingStructuredDocumentProvider`、标准化模型和序列化 | 完成 |
| F1 | Docling 低并发独立 Worker | `hpagent-document-worker`、`DOCUMENT_TASK_QUEUE`、`max_concurrent_activities=1` | 完成 |
| F1 | Temporal History 只保存 compact refs | Workflow 返回 `NormalizedDocumentRef`；完整结构写入 `normalized_documents` | 完成 |
| F1 | 大文档自动路由 | 普通 Agent 按文件大小和复杂 MIME 确定性选择 bounded direct read 或 Document Worker；History 只接收 compact ref | 完成 |
| F1 | Trace / Run Budget | `DocumentNormalization` Trace；bytes scanned 与 wall time reserve/settle；重放会收敛未结算 reservation | 完成 |
| F2 | 创建 DOCX/XLSX/PPTX | `DocxWriter`、`XlsxWriter`、`PptxWriter` 和三个声明式工具 | 完成 |
| F2 | Gotenberg 转 PDF | 常驻 Compose service；`GotenbergConversionProvider`；按需调用而非在 Agent Worker 管理 LibreOffice/Chromium | 完成 |
| F2 | 不可变输出发布 | `OutputPublisher` 从 `outputs/` 发布到 `TenantFileStore`，登记 `stored_files/run_files`，同 operation 得到同 file id | 完成 |
| F2 | ACK 丢失重试 | adapter 写入前按稳定 `(run_id, operation_id)` replay 已发布结果，返回原 `file_id/version`，不重复登记或扣预算 | 完成 |
| F2 | 输出可见性 | Run 完成事务把 output 绑定到 completed assistant message，之后才可通过认证下载 | 完成 |
| F3 | 文件版本和 lineage | `parent_file_id`、数据库计算 `version`、ready lineage 不可修改、root-first lineage API | 完成 |
| F3 | PDF 转换 lineage | `convert_file_to_pdf` 发布时记录 source `file_id` 为 `parent_file_id`，lineage API 返回 source → PDF | 完成 |
| F3 | 声明式 patch | DOCX replace/append、XLSX range write、PPTX single-slide replace；全部生成新版本 | 完成 |
| F4 | 高风险动作审批数据模型 | `FileActionApproval` 和 `file_action_approvals` 状态机 | 完成 |
| F4 | 审批 API 与命令幂等 | owned list、approve、reject；CSRF + Idempotency-Key；同租户校验 | 完成 |
| F4 | 单次精确授权 | grant 绑定 run、operation、tool、arguments SHA-256，Worker 原子 consume 一次 | 完成 |
| F4 | Workflow 暂停等待审批并恢复 | ToolExecutionWorkflow、Approval Outbox、Temporal Signal、authoritative reload | 完成 |
| F4 | Persistent Web File overwrite | production save_persistent_file、fencing、CAS、reconciliation、Web Approval Card + Playwright composition E2E | 完成 |

## 3. 技术选型落实情况

| 指定技术 | 代码落点 | 审计结论 |
|---|---|---|
| MarkItDown | `src/file_adapters/markitdown.py` | 已实际使用 |
| Docling | `src/file_adapters/docling.py`、Document Worker | 已实际使用 |
| pypdf | `PdfAdapter.inspect/read_pages` | 已实际使用 |
| pypdfium2 | requirements | **只有依赖，当前 Adapter 未调用** |
| pdfplumber | `PdfAdapter.extract_tables` | 已实际使用 |
| python-docx | DOCX read/write/patch adapters | 已实际使用 |
| python-calamine | `XlsxAdapter` 只读 | 已实际使用 |
| openpyxl | XLSX create/range patch | 已实际使用 |
| python-pptx | PPTX read/create/slide patch | 已实际使用 |
| Gotenberg | Compose + `GotenbergConversionProvider` | 已实际使用并做过真实转换 |

未引入普通 parsing library 微服务；只有需要独立生命周期或资源隔离的 Gotenberg 与
Docling Worker 被拆分。没有 GPU 要求，也没有商业 Document API P0 依赖。

## 4. Domain、Provider 与 Adapter

### Domain

- `src/file_domain/models.py`
  - `FileResource`：已解析且属于当前 Run 的本地资源。
  - `SourceLocator`：页、段落、sheet/range、slide 等来源定位。
  - `NormalizedDocument/Block/Table`：跨格式标准结构。
  - `TextView`：有界快速文本结果。
  - `DocumentPatch`：派生编辑的父文件、操作与 patch 描述。
- `src/file_domain/approvals.py`
  - `FileActionApproval`：高风险动作授权引用。
  - `FileActionApprovalService`：request/list/decide/consume。

### Provider

- `FastTextViewProvider` -> `MarkItDownFastTextViewProvider`
- `StructuredDocumentProvider` -> `DoclingStructuredDocumentProvider`
- `ConversionProvider` -> `GotenbergConversionProvider`

### Format Adapter

- PDF：metadata、显式 page range、单页 table extraction。
- DOCX：paragraph range 和单表读取。
- XLSX：sheet metadata 和受 `max_cells` 限制的 range。
- PPTX：slide metadata 和单页读取。

## 5. Tool Registry 与语义

### Read-only

- `read_file_text`
- `inspect_pdf` / `read_pdf_pages` / `read_pdf_table`
- `inspect_docx` / `read_docx_paragraphs` / `read_docx_table`
- `inspect_workbook` / `read_sheet_range`
- `inspect_presentation` / `read_slide`
- 原有 `inspect_file` / `search_file` / `count_matches` / `text_stats`

### Idempotent write

- `create_docx`
- `create_workbook`
- `create_presentation`
- `convert_file_to_pdf`
- `replace_docx_text`
- `append_docx_section`
- `write_sheet_range`
- `replace_slide`

这些工具只创建 immutable output，不能覆盖 input 或任意外部路径。稳定 `operation_id`
同时用于 Durable Activity intent、预算账本与 output publication 去重。

### Non-idempotent write

`save_persistent_file` 是当前唯一注册的高风险持久化写工具：目标不存在时安全创建 revision 1；
目标存在时由 trusted runtime 固定 expected revision/hash 并进入 durable approval wait。批准后通过
revision CAS、fencing 和 operation intent 执行 immutable revision 写入，ACK 丢失时按
`last_operation_id/current_sha256/current_revision` reconciliation。delete/send 仍未注册。

## 6. Persistence 与 migration

| Migration | 内容 | 当前数据库 |
|---|---|---|
| 025 | `normalized_documents`、operation 幂等、Run/File FK、API/Worker 权限 | 已应用 |
| 026 | 将 normalized document FK 强化为 owned run-file 复合关系 | 已应用 |
| 027 | `stored_files.parent_file_id/version`、同租户父 FK、数据库版本计算 trigger | 已应用 |
| 028 | ready 文件 lineage 不可修改 | 已应用 |
| 029 | `file_action_approvals`、审批状态机、精确 grant、审批命令幂等操作 | 已应用，checksum 已记录 |
| 030 | Research Markdown output publication composition | 已应用，checksum 已记录 |
| 031 | Persistent destination/revision、recoverable approval execution binding | 已应用，checksum 已记录 |
| 032 | Approval decided Outbox event 与 cancelled 状态扩展 | 已应用，checksum 已记录 |

所有后续 schema 修正必须新增 030+；不得修改上述已应用 migration 或手工修改
`schema_migrations.checksum`。

## 7. Trace、Budget、Side Effect 与兼容性

- 文件 read/write 工具继续经过 `DurableAgentActivities.tool_execution`，保留执行 lease、
  fencing token、transcript version、Operation Intent 和 reconciliation。
- 文件 Trace 节点使用 `FileInspect/FileSearch/FileCount/FileTransform`；只写白名单统计和引用。
- tool metadata 声明 bytes scanned/written/output/returned；Run Budget 统一 reserve/settle。
- Document Activity 使用独立的 `DocumentNormalization` Trace 和相同 Run Budget 数据面。
- `RunFileScope` 保留旧逻辑名解析，同时加入 UUID 和已发布 output 解析；旧错误文本相关测试仍在。
- Worker 在发布后、Activity ACK 前退出时，重试先 replay 已有 publication；同一 operation
  复用原 file/version 和预算 operation。SIGKILL 遗留的同 Run workspace 会从 immutable store 重建。
- 未删除 Research、QQ/NapCat、legacy replay-sensitive workflow 或早期 migration。
- `.serena/` 是本地搜索缓存，已加入 `.gitignore`，没有进入提交。

## 8. Docker 与依赖

- Gotenberg 使用固定 digest，常驻 service，health endpoint 映射到本机 3001。
- Document Worker 使用 `src/Dockerfile.document` 和独立 requirements，Docling 默认单 Activity
  并发；不要求 GPU。
- Gotenberg、数据库、Temporal 等内部地址加入相关 `NO_PROXY`。
- `WEB_FILE_TRANSFORM_ENABLED` 默认 false，必须同时启用 Durable Agent 与 file upload。
- `WEB_FILE_SHELL_ENABLED` 默认 false，生产环境明确拒绝 host Bash。
- `FILE_DIRECT_READ_MAX_BYTES` 默认 1 MiB；复杂文档或超过阈值的文件路由到 Document Worker。

## 9. 测试证据

本次最终记录：

| 测试层 | 结果 |
|---|---|
| Binary Web upload → message → RunFileScope（PostgreSQL） | PASS；PDF/DOCX/XLSX/PPTX 与 UTF-8 log |
| Output ACK-gap retry + PDF lineage（PostgreSQL） | PASS；合并 persistence 组 7 passed |
| Durable retry/budget + scoped unit | PASS；43 passed |
| Document real Temporal integration | PASS；2 passed |
| Agent/Research Temporal contract regression | PASS；62 passed |
| 全量非 external、非 PostgreSQL | 511 passed, 17 skipped, 172 deselected |
| Ruff scoped | PASS |
| `git diff --check` | PASS |
| `docker compose config --quiet` | PASS |

核心测试文件：

- `test/test_file_assistant_f0.py`
- `test/test_file_read_tools.py`
- `test/test_file_output.py`
- `test/test_file_version_tools.py`
- `test/test_document_worker.py`
- `test/test_document_temporal_integration.py`
- `test/test_file_action_approval_contract.py`
- `test/web_persistence/test_document_worker_persistence.py`
- `test/web_persistence/test_file_output_persistence.py`
- `test/web_persistence/test_file_lineage.py`
- `test/web_persistence/test_file_action_approvals.py`
- `test/web_api/test_binary_file_upload.py`
- `test/test_durable_agent_hardening.py`
- `test/test_run_file_workspace.py`

## 10. 已知漂移与风险

### 明确漂移

1. **F4 provider 边界**：当前仅支持 Web Persistent File create/overwrite；external provider、
   delete/send 不在本阶段范围。
2. **审批 UI 是 Run 内卡片**：没有独立全局 approval inbox；刷新/重连由 owned approval list
   恢复，审批结果和 destination revision 均以 PostgreSQL/API 真值为准。
3. **pypdfium2 尚未进入代码路径**：依赖已安装，当前 PDF text/metadata 走 pypdf，table 走
   pdfplumber；扫描 PDF 的 render/OCR fallback 尚未实现。

### 非漂移的有意边界

- Playwright 属于 Research browser fallback，不用于文档解析。
- Gotenberg 是唯一 conversion service；python-docx/openpyxl/python-pptx 不拆微服务。
- 所有 patch 生成新版本，不原地编辑 input。
- 当前安全派生工具不需要人工审批。

## 11. 本轮边界

F0–F3 关闭 binary upload、idempotent write ACK-gap、PDF conversion lineage 和大型文档
deterministic routing。F4 在同一 Durable Agent/side-effect runtime 上增加 Persistent Web File
create/overwrite、durable approval wait/signal、精确可恢复授权、CAS/fencing/reconciliation，
以及 Run 内 Web Approval Card；未新增 external provider、delete/send、File Agent 或 F5。

## 12. 审计结论

F0–F3 Runtime Complete，已形成“正式 binary upload—RunFileScope—有界读取/确定性 Document
Worker 路由—不可变幂等输出—版本 lineage”的运行时闭环。
F4 已完成首次安全创建、overwrite approval、Temporal durable wait/resume、精确 grant、revision
CAS、fencing 和 ACK-loss reconciliation；Run 内审批卡、状态恢复与 destination 下载链接已实现。
Playwright 验收从真实 Web Chat 上传和发送请求，经 deterministic OpenAI-compatible model 响应解析、
production Tool Registry、Approval API/Outbox/Temporal resume 写入 revision 2，并通过当前浏览器登录态
下载且校验批准后的正文。
