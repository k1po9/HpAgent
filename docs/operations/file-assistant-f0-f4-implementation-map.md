# File Assistant F0–F4 实现地图与漂移审计

审计基线：`feat/hpagent-web`，实现提交 `321dd2c`。

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
| F0 | PDF / DOCX / XLSX / PPTX 有界读取 | `PdfAdapter`、`DocxAdapter`、`XlsxAdapter`、`PptxAdapter`；页、表、段落、range、slide 均要求显式范围 | 完成 |
| F0 | Tool Registry 与副作用分类 | 文件读工具注册为 `read_only`，需要 file scope，并声明预算字段 | 完成 |
| F1 | Docling 结构化文档 | `DoclingStructuredDocumentProvider`、标准化模型和序列化 | 完成 |
| F1 | Docling 低并发独立 Worker | `hpagent-document-worker`、`DOCUMENT_TASK_QUEUE`、`max_concurrent_activities=1` | 完成 |
| F1 | Temporal History 只保存 compact refs | Workflow 返回 `NormalizedDocumentRef`；完整结构写入 `normalized_documents` | 完成 |
| F1 | Trace / Run Budget | `DocumentNormalization` Trace；bytes scanned 与 wall time reserve/settle；重放会收敛未结算 reservation | 完成 |
| F2 | 创建 DOCX/XLSX/PPTX | `DocxWriter`、`XlsxWriter`、`PptxWriter` 和三个声明式工具 | 完成 |
| F2 | Gotenberg 转 PDF | 常驻 Compose service；`GotenbergConversionProvider`；按需调用而非在 Agent Worker 管理 LibreOffice/Chromium | 完成 |
| F2 | 不可变输出发布 | `OutputPublisher` 从 `outputs/` 发布到 `TenantFileStore`，登记 `stored_files/run_files`，同 operation 得到同 file id | 完成 |
| F2 | 输出可见性 | Run 完成事务把 output 绑定到 completed assistant message，之后才可通过认证下载 | 完成 |
| F3 | 文件版本和 lineage | `parent_file_id`、数据库计算 `version`、ready lineage 不可修改、root-first lineage API | 完成 |
| F3 | 声明式 patch | DOCX replace/append、XLSX range write、PPTX single-slide replace；全部生成新版本 | 完成 |
| F4 | 高风险动作审批数据模型 | `FileActionApproval` 和 `file_action_approvals` 状态机 | 完成 |
| F4 | 审批 API 与命令幂等 | owned list、approve、reject；CSRF + Idempotency-Key；同租户校验 | 完成 |
| F4 | 单次精确授权 | grant 绑定 run、operation、tool、arguments SHA-256，Worker 原子 consume 一次 | 完成 |
| F4 | Workflow 暂停等待审批并恢复 | 当前没有 destructive file tool，因此没有 Signal/Outbox 唤醒链路，也没有把普通输出工具暂停 | **未实现** |
| F4 | 外部 overwrite/delete/send | Tool Registry 中不存在这些工具 | **未实现** |

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

当前没有注册 external overwrite/delete/send 工具。F4 的 approval grant 只是它们未来接入前的
持久化安全边界，不能把它描述成已完成的端到端 HITL。

## 6. Persistence 与 migration

| Migration | 内容 | 当前数据库 |
|---|---|---|
| 025 | `normalized_documents`、operation 幂等、Run/File FK、API/Worker 权限 | 已应用 |
| 026 | 将 normalized document FK 强化为 owned run-file 复合关系 | 已应用 |
| 027 | `stored_files.parent_file_id/version`、同租户父 FK、数据库版本计算 trigger | 已应用 |
| 028 | ready 文件 lineage 不可修改 | 已应用 |
| 029 | `file_action_approvals`、审批状态机、精确 grant、审批命令幂等操作 | 已应用，checksum 已记录 |

所有后续 schema 修正必须新增 030+；不得修改上述已应用 migration 或手工修改
`schema_migrations.checksum`。

## 7. Trace、Budget、Side Effect 与兼容性

- 文件 read/write 工具继续经过 `DurableAgentActivities.tool_execution`，保留执行 lease、
  fencing token、transcript version、Operation Intent 和 reconciliation。
- 文件 Trace 节点使用 `FileInspect/FileSearch/FileCount/FileTransform`；只写白名单统计和引用。
- tool metadata 声明 bytes scanned/written/output/returned；Run Budget 统一 reserve/settle。
- Document Activity 使用独立的 `DocumentNormalization` Trace 和相同 Run Budget 数据面。
- `RunFileScope` 保留旧逻辑名解析，同时加入 UUID 和已发布 output 解析；旧错误文本相关测试仍在。
- 未删除 Research、QQ/NapCat、legacy replay-sensitive workflow 或早期 migration。
- `.serena/` 是本地搜索缓存，已加入 `.gitignore`，没有进入提交。

## 8. Docker 与依赖

- Gotenberg 使用固定 digest，常驻 service，health endpoint 映射到本机 3001。
- Document Worker 使用 `src/Dockerfile.document` 和独立 requirements，Docling 默认单 Activity
  并发；不要求 GPU。
- Gotenberg、数据库、Temporal 等内部地址加入相关 `NO_PROXY`。
- `WEB_FILE_TRANSFORM_ENABLED` 默认 false，必须同时启用 Durable Agent 与 file upload。
- `WEB_FILE_SHELL_ENABLED` 默认 false，生产环境明确拒绝 host Bash。

## 9. 测试证据

本次最终记录：

| 测试层 | 结果 |
|---|---|
| F4 contract + PostgreSQL | 4 passed |
| File/Document/Durable scoped | 64 passed, 1 skipped（未提供 Temporal env 的那次） |
| File output + lineage PostgreSQL | 4 passed |
| Document Worker PostgreSQL | 1 passed |
| Document real Temporal integration | 1 passed |
| 全量非 external、非 PostgreSQL | 507 passed, 15 skipped, 170 deselected |
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

## 10. 已知漂移与风险

### 明确漂移

1. **F4 不是完整 HITL Workflow**：有审批状态/API/精确单次授权，但没有 Temporal wait/signal、
   approval Outbox 或审批后恢复 Activity。
2. **没有 destructive Adapter**：这符合当前“不虚构外部目标”的安全选择，但也意味着
   overwrite/delete/send 尚不可验收。
3. **pypdfium2 尚未进入代码路径**：依赖已安装，当前 PDF text/metadata 走 pypdf，table 走
   pdfplumber；扫描 PDF 的 render/OCR fallback 尚未实现。
4. **Document normalization 尚未由通用 Agent 自动规划触发**：Workflow/Worker/Activity 可运行，
   但当前 Agent 文件读工具主要直接使用有界 Adapter；大型文档自动路由策略仍需补齐。
5. **前端没有 approval inbox/按钮**：审批 REST API 已有，Web UI 尚未实现。

### 非漂移的有意边界

- Playwright 属于 Research browser fallback，不用于文档解析。
- Gotenberg 是唯一 conversion service；python-docx/openpyxl/python-pptx 不拆微服务。
- 所有 patch 生成新版本，不原地编辑 input。
- 当前安全派生工具不需要人工审批。

## 11. 下一阶段建议

按依赖顺序：

1. 定义首个真实高风险文件动作及外部目标 Adapter，确认 reconciliation 能查询目标端状态。
2. 增加 `request_file_action_approval` Outbox 事件和 Temporal Signal；Workflow 等待时保持 Run
   非终态，并支持批准、拒绝、超时和取消。
3. 在 Side Effect Intent 之前原子 consume grant；在外部调用前再次校验 fencing token。
4. 增加 Worker-kill 测试：批准前、consume 后/intention 前、外部 ACK 丢失三个故障窗口。
5. 实现 Web approval inbox，并展示 tool、action summary、目标、过期时间；禁止展示敏感参数。
6. 为扫描 PDF 接入 pypdfium2 render fallback；若引入 OCR，单独明确预算与低并发策略。
7. 增加大型文档的自动路由规则，使普通 Agent 选择 compact `NormalizedDocumentRef` 而不是
   把整份文档送入上下文。

## 12. 审计结论

F0–F3 已形成可运行的“有界读取—独立结构化 Worker—不可变输出—版本 lineage”闭环。
F4 已形成持久化审批安全基础，但端到端 HITL 仍是部分完成。评审时应重点核对第 10 节，
避免把存在类名、表或 API 误判为完整运行时能力。
