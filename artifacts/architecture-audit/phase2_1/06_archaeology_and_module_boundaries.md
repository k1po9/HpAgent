# 架构演进与模块边界

这是根据本地 git 历史和当前源码得到的演进线索，不把 commit 标题当成当前功能已完成的证据。完整最近60条记录见 history_evidence.tsv；实现断言仍引用本轮静态/注册事实。

| 演进点 | 提交 / 日期 | 当前留下的边界 |
| --- | --- | --- |
| Hand/Brain 分离 | 4304082 / 2026-07-11 | agent/protocol.py 的生产 DTO，brain 与 actions 分工 |
| Web API / fake executor | 89ecb0a / 2026-08-05 | web_api 与 web_domain；fake 开发门禁仍在 |
| 共享执行收口 | 2520098 / 2026-08-12 | bootstrap/qq、QQ/Web Host、Facade、DefaultBrainActionLoop；旧 closure 文档反映这个阶段 |
| Durable Temporal Agent | 0871d5c / 2026-08-17 | agent_workflows / agent_activities / AgentDataStore；新增控制流，不等于重命名旧 loop |
| Trace | 0de2f29 等 / 2026-08-22 | agent_execution/tracing 与前端 Trace panel |
| File scope / budget | d94f2ed、ab6f0f4 等 / 2026-08-26至29 | tenant store、Run scope、budget ledger、文件安全门禁 |
| Research R0–R6 | 060b3d4 / 2026-09-02 | Research 四层与固定 stage workflow |
| File/document F0–F4 | 321dd2c / 2026-09-03 | adapters/domain/runtime、独立 Document Worker、格式处理与 lineage |
| Approval / persistent overwrite | 5a90c53、78bac19、e79e816 / 2026-09-04 | ToolExecutionWorkflow approval wait、persistent destinations/revisions、操作恢复 |
| Research 附件发布 | cd7e0ae / 2026-09-04 | ResearchMarkdownPublisher 复用 OutputPublisher，但 Report Artifact 仍有独立发布路径 |
| 工具能力路由 | 2f6e39f、4cd2fc1 / 2026-09-06 | sandbox/tools/routing 明确 selection policy/projector/contracts |
| token accounting / SSE 修正 | d7b376f、44f4514、4ab07fb / 2026-09-07至08 | provider attempt 用量、Run snapshot、前端 token 展示 |

## 当前最重要的五个分界

1. 执行策略：QQ + Web legacy 走 Facade/DefaultBrainActionLoop；Durable Web 走 Temporal Agent workflows。共用 Brain/Action 基础设施，不共享全部控制流。
2. 工作进程：主 Worker 组合 QQ 与 Web 两个 Temporal Workers；API 独立；Document Activity Worker 独立；migration 一次性；standalone Web 受拓扑门禁约束。
3. 数据所有权：PG identity/Web/durable/Research/file state；QQ Redis/WAL；SQLite workspace metadata；Git workspace；tenant objects；document scratch；Hindsight long-term memory。不能按目录相似度归并。
4. 文件与成果：上传输入、Run output、persistent revision、Research report、HTML Artifact version 是不同对象和生命周期；它们通过 ID/lineage/事务连接。
5. 产品可用面：后端 P1–P6/S1/S2 实现资产存在，不等于每个前端界面都完整。Research UI 的缺口是当前最清晰的 surface 不对称；Plan-and-Execute 只在 durable gate 开启时对 Web 可选。

## 当前最大组合热点的职责簇

worker.py：init_dependencies 负责模型/凭证、Redis、workspace、MCP/Skills/RAG、file capability、scheduler、Hindsight、QQ；compose_web_workers 负责 Web Host、durable Activities、Research providers、Artifact、dispatcher/reconciler；start_worker 负责 QQ Worker、条件 Web workers、channel listeners、cleanup、周期 schedules；_shutdown_worker_resources 负责协程和连接资源关闭。以上职责分别在 symbol 行及调用边中记录，未提出拆分方案。

MCPToolManager 所在 mcp.py 是当前最大后端文件。它需要同时处理服务连接和工具适配/投影；远端工具列表由连接后动态获得，仓库只能证明发现/注册/调用机制，不能离线穷尽工具行为。

## 数据与副作用边界

| 系统 | 资源 | 逻辑 owner | 生命周期 | 证据 | 能力 |
| --- | --- | --- | --- | --- | --- |
| PostgreSQL identity | accounts;identity_bindings;web_credentials;identity_binding_challenges;web_auth_sessions | account/*;web_api/auth.py | API registration/login/binding; QQ identity lookup | src/account/postgres_account_service.py:63;src/web_api/app.py:273;src/account/registration_service.py:1 | P2 |
| PostgreSQL Web transaction | conversations;messages;runs;sessions;workflow_executions;idempotency_commands;outbox_events | web_domain;web_api/queries.py;persistence/repositories.py | API writes requests and outbox; Worker commits lifecycle; API projects query/SSE | src/web_domain/services.py:206;src/web_domain/lifecycle.py:1;src/web_api/queries.py:1 | P1;P4;S2 |
| PostgreSQL durable data plane | agent_transcripts;agent_transcript_events;agent_operations;account_execution_leases | agent_activities/store.py | Activity CAS/idempotency/fencing, lease ownership per account | persistence/migrations/014_durable_agent_control_plane.sql:12;src/agent_activities/store.py:1 | P3;P4 |
| PostgreSQL budget / trace | run_budgets;run_usage_ledger;trace_runs;trace_events | agent_execution/run_budget.py;agent_execution/tracing/repository.py | model/provider attempts, tool/research/document budgets; Trace projection | src/agent_execution/run_budget.py:1;persistence/migrations/017_file_workspace_p0.sql:89;persistence/migrations/016_agent_trace.sql:5 | P4;S2 |
| PostgreSQL files | stored_files;message_files;run_files;normalized_documents;file_action_approvals;persistent_file_destinations;persistent_file_revisions | web_domain/file_services.py;file_domain/*;file_runtime/output.py;workspace/file_scope.py | upload metadata, run bindings, normalization, approvals, immutable revisions/lineage | persistence/migrations/017_file_workspace_p0.sql:4;persistence/migrations/025_file_document_worker.sql:4;persistence/migrations/031_persistent_web_file_foundation.sql:28 | P5 |
| PostgreSQL Research | tasks;research_plans;source_records;source_contents;evidence_items;research_stage_results;research_iterations;research_claims;research_citations;research_reports | research_domain/persistence.py;research_domain/services.py | fixed stage results/idempotency; schedule desired state; report publication via SQL function | src/research_domain/services.py:184;src/research_domain/persistence.py:526 | P6 |
| PostgreSQL Artifact | artifacts;artifact_versions;artifact_outbox_events | web_artifacts/*;orchestration/artifact_*;Research SQL publication | message-driven build/version queue; Research publishes report artifact with deterministic IDs | src/web_artifacts/services.py:211;src/research_domain/persistence.py:526 | S1;P6 |
| Redis QQ | session hot state;group context | session/store.py;memory/group_context.py;storage/redis.py | short-term state/cache; SessionStore also owns WAL and optional in-memory fallback | src/session/store.py:63;src/orchestration/worker.py:664 | P1;P2 |
| Redis Web | online progress/delta/trace;terminal event channel | agent_execution/web_events.py;web_api/sse.py;web_api/terminal_publisher.py | best-effort online transport; authoritative Run remains PostgreSQL | src/web_api/app.py:322;src/web_api/terminal_publisher.py:74 | S2 |
| SQLite workspace | workspace.db:users;sessions | session/db.py WorkspaceDB | local workspace metadata, distinct from PostgreSQL accounts/sessions | src/session/db.py:15;src/orchestration/worker.py:715 | P1;P3 |
| QQ WAL / archive files | active-session WAL/checkpoint;session history.jsonl/meta.yaml | session/store.py;application/session_archive.py;storage/file_store.py | append event WAL then cache; archive orchestrated by application service | src/session/store.py:204;src/application/session_archive.py:1 | P2;P4 |
| Git workspace | account repo;session branches | sandbox/git_repo.py;session/workspace.py;workspace/isolation.py | GitRepoManager prepares repo/branch; process lock and account lock protect topology | src/application/conversation.py:195;src/workspace/isolation.py:251 | P3;P4 |
| Tenant object files | FILE_STORE_ROOT;hpagent-file-store volume | storage/tenant_file_store.py | immutable account/file object paths; API/Worker writable, Document worker reader | docker-compose.yaml:607;src/orchestration/document_worker.py:31 | P5 |
| Run file execution scope | RUN_FILE_ROOT / hpagent-file-runs;DOCUMENT_RUN_ROOT / hpagent-document-runs | workspace/file_scope.py;file_runtime/resolver.py | bind logical inputs; isolate run output and document scratch from persistent objects | src/workspace/file_scope.py:1;docker-compose.yaml:608 | P5 |
| Scheduler JSON | scheduler.data_dir | orchestration/scheduler.py | user_reminder task persistence and handler polling; separate from Temporal Research schedules | src/orchestration/worker.py:751;src/orchestration/worker.py:891 | P1 |
| Hindsight | bank / retained documents / recall results | memory/hindsight_client.py;application/memory*.py | long-term memory external service; Web retain consumer waits for healthy Hindsight | src/orchestration/worker.py:373;src/application/memory_retention.py:1 | P2 |
| Temporal | workflow histories;queues;schedules | orchestration/*;agent_workflows/* | History holds compact references; business payloads/transcripts in PostgreSQL; external histories not inspected | src/orchestration/web_workers.py:163;src/agent_workflows/react.py:1 | P4;P6 |
