# 入口与组合根

识别 7 个生产相关逻辑入口（其中 R5 是受门禁约束的备用进程入口），加 R8 migration，共 8 个命名 root。R3/R4 是主进程内部独立 Temporal Worker/queue，并非两个独立 Docker 进程；不要把 7 理解成 7 个同时运行的服务。

| ID | 入口 | 种类 | 启用条件 | 证据 |
| --- | --- | --- | --- | --- |
| R1 | Main Worker / QQ / DEPLOY:hpagent | production_process | valid config; agent.mode=single; selected channel | src/entrypoint.sh:5 |
| R2 | Web API / DEPLOY:hpagent-api | production_process | valid WebSettings / database | src/Dockerfile.web-api:10 |
| R3 | Web lifecycle Worker + background consumers / ROOT:WEB_LIFECYCLE | production_worker | WEB_REAL_AGENT_ENABLED; C-07 gate; database/topology checks | src/orchestration/worker.py:930 |
| R4 | Web Agent Worker / ROOT:WEB_AGENT | production_worker | WEB_REAL_AGENT_ENABLED; new durable starts additionally DURABLE_AGENT_ENABLED | src/orchestration/web_workers.py:180 |
| R5 | Standalone Web Worker / src/orchestration/web_worker.py::main | conditional_process | single_process_account_lock REJECTED; session_worktree structurally admitted but not implemented isolation | src/orchestration/web_worker.py:93 |
| R6 | Document Activity Worker / DEPLOY:hpagent-document-worker | production_process | database / Temporal / document image; heavy-normalization request | src/Dockerfile.document:20 |
| R7 | Browser frontend / DEPLOY:web-gateway | production_browser | web-gateway web-prod; web-dev development | web/src/main.tsx:1 |
| R8 | SQL migration runner / DEPLOY:hpagent-migrate | ops_process | MIGRATE_DATABASE_URL | src/Dockerfile.migrate:17 |


## Compose → process

| Service | Profiles | Dockerfile | Command / entrypoint | 证据 |
| --- | --- | --- | --- | --- |
| app-postgres | (default) |  |   | docker-compose.yaml:15 |
| redis | (default) |  |   | docker-compose.yaml:48 |
| searxng | (default) |  |  ["/bin/sh", "/usr/local/bin/hpagent-searxng-entrypoint.sh"] | docker-compose.yaml:79 |
| gotenberg | (default) |  | ["gotenberg", "--api-port=3000", "--libreoffice-auto-start=true", "--chromium-auto-start=true"]  | docker-compose.yaml:119 |
| temporal-postgres | (default) |  |   | docker-compose.yaml:139 |
| temporal | (default) |  |   | docker-compose.yaml:174 |
| hindsight-postgres | (default) |  | -c shared_buffers=128MB -c work_mem=8MB -c maintenance_work_mem=64MB -c effective_cache_size=256MB -c wal_buffers=16MB -c max_wal_size=1GB   | docker-compose.yaml:220 |
| hindsight | (default) |  |   | docker-compose.yaml:268 |
| hpagent-migrate | agent;web;web-prod | src/Dockerfile.migrate | CMD ["python", "-m", "persistence.migrate"]  | docker-compose.yaml:353 |
| hpagent | agent;web;web-prod | src/Dockerfile | ENTRYPOINT ["/entrypoint.sh"]  | docker-compose.yaml:410 |
| f4-e2e-model | f4-e2e |  | ["python", "/fixture/f4_e2e_model.py"]  | docker-compose.yaml:561 |
| hpagent-document-worker | agent;web;web-prod | src/Dockerfile.document | CMD ["python", "-u", "-m", "orchestration.document_worker"]  | docker-compose.yaml:584 |
| hpagent-api | web;web-prod | src/Dockerfile.web-api | CMD ["python", "-m", "web_api"]  | docker-compose.yaml:635 |
| web-dev | web |  | ["sh", "-c", "if [ ! -x node_modules/.bin/vite ]; then\n  npm ci\nfi\nexec npm run dev -- --host 0.0.0.0\n"]  | docker-compose.yaml:746 |
| web-gateway | web-prod | web/Dockerfile |   | docker-compose.yaml:801 |
| napcat | qq |  |   | docker-compose.yaml:852 |
| temporal-web | tools |  |   | docker-compose.yaml:897 |


hpagent 的 build context 是 ./src；Dockerfile 的 ENTRYPOINT→/entrypoint.sh→python -u -m main。API 和 migration 从根目录构建并设置 PYTHONPATH=/app/src。Document 从 ./src 构建自己的依赖环境。web-dev 是 Vite 开发服务器，web-gateway 的 Nginx 提供静态前端并代理 /api、/auth 和 Run SSE。

## Queue 注册矩阵

| 进程 / Worker | Queue | Workflow | Activity / 后台消费者 |
| --- | --- | --- | --- |
| R1 QQ Worker | config.temporal.task_queue（默认 hpagent-task-queue） | OrchestrationWorkflow, ReflectWorkflow, MetricsReportWorkflow | process_turn, archive_session, reflect, reflect_batch, metrics_report |
| R3 lifecycle | hpagent-web-lifecycle | WebRunWorkflow, DurableWebRunWorkflow, ResearchReportWorkflow, ResearchTaskScheduleWorkflow, ArtifactBuildWorkflow, NormalizeDocumentWorkflow | Web lifecycle/finalize, Research固定阶段, Artifact build |
| R4 agent | hpagent-web-agent | AgentRunWorkflow, ReactAgentWorkflow, PlanAndExecuteWorkflow, AgentStepWorkflow, ToolExecutionWorkflow | legacy execute_agent, context/model/tool/planning/evaluate/approval/persistent-overwrite |
| R6 document | hpagent-document | 无 Workflow 注册 | normalize_document_activity，max_concurrent_activities=1 |

证据：worker.py:910、318、343；web_workers.py:160、180；document_worker.py:22。共 14 个 Workflow definition、37 个 Activity definition；definition 数量与 Worker 数量、调用次数不同。

`temporal_and_dynamic_registrations.csv` 保存装饰器定义、实际 registry list 与调用点三类事实；`reviewed_runtime_edges.csv` 补足 Research stage table、strategy target 变量和 DI 回调，避免只统计 import。已补证主要生产字符串 Activity 到 definition 的映射；原始扫描中的变量项与测试 worker 单独保留，不把字符串扫描本身当成运行验证。

## 关键门禁

| 配置 | 源码 / 仓库值 | 运行影响 |
| --- | --- | --- |
| WEB_REAL_AGENT_ENABLED | dataclass 默认 false；Compose hpagent 默认 true | 开启同进程 R3/R4 及 Web 后台消费者；校验 C-07、数据库、队列/超时冻结合同 |
| DURABLE_AGENT_ENABLED | config.yaml false；Compose fallback false | 只控制新 Chat Run 的 Workflow 选择和 API plan_and_execute 可用性；不控制历史 definition 注册 |
| WORKSPACE_ISOLATION_MODE | dataclass 空串要求显式配置；Compose fallback single_process_account_lock | 当前支持的共享锁拓扑；standalone 被拒绝；session_worktree 的完整实现未证 |
| agent.mode | YAML single | 非 single 直接 RuntimeError，agents.yaml 仍会被加载 |
| channels.enabled | dataclass [console]；YAML [napcat] | factory 仅 NapCat/OfficialQQ，console 被 skip |
| WEB_FAKE_EXECUTOR_ENABLED | 默认 false；API Compose false | development 中可开；production settings 明确拒绝 |
| WEB_FILE_UPLOAD_ENABLED / TRANSFORM / SHELL | 各有配置验证 | 决定上传/工具/转换；transform 还要求 durable 与 Gotenberg |
| hindsight.enabled / client 可用性 | YAML true；初始化可降级 | 长期记忆和 retain_memory 消费需要有效客户端 |
| scheduler.enabled | YAML true | user_reminder load/inject/poll；handler 的注册本身无该 gate |
| models.tool_rag / mcp / skills | 分别按配置开启 | 向量检索、MCP连接、Skill加载；外部工具列表运行时变化 |

更多 getenv 位置见 config_gates.csv；文件行还记录 proof 路径上的 if 分支。没有用私有 .env 推断实际部署值。
