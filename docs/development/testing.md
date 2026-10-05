# 测试

## 测试层与环境隔离

| 层 | 证明内容 | 不证明内容 |
| --- | --- | --- |
| Unit / contract | 需求与策略、Provider 请求/分类、预算、上下文、SSE 状态和沙箱 import。 | 真实 Provider / QQ 行为。 |
| PostgreSQL / API | 真实角色权限、事务、revision / epoch、lease / receipt、预算、授权与跨账户隔离。 | 真实外部网络和发送。 |
| Temporal / replay / SIGKILL | 指定配置下 Workflow、attempt 恢复、去重与取消。 | 未使用的生产 lease / timeout 配置。 |
| Chromium | 实际页面和真实 API / PG / Redis / 文件服务。 | 测试执行器覆盖的模型或 Artifact 供应商。 |
| Live acceptance | 真实模型工具往返、渠道、生产恢复和压力。 | 不能由上面的通过次数自动替代。 |

PG/API fixtures 会 TRUNCATE 业务表，浏览器会写入账户与执行数据。**先建立专用可丢弃测试库，不使用当前 root 业务库。** 三个 DSN 必须指向同一测试数据库的 migration / API / worker 角色，Redis 选择独立 DB，文件目录与端口也隔离。Makefile 和 E2E 脚本的默认 DSN 指向 hpagent，不能直接沿用来验证共享环境。

```bash
# 替换三个占位 DSN；测试库须事先建立，使用实际角色凭据。
export MIGRATION_DATABASE_URL='postgresql://MIGRATION_ROLE:PASSWORD@localhost:5434/hpagent_test'
export APP_DATABASE_URL='postgresql://API_ROLE:PASSWORD@localhost:5434/hpagent_test'
export WORKER_DATABASE_URL='postgresql://WORKER_ROLE:PASSWORD@localhost:5434/hpagent_test'
export HPAGENT_MIGRATIONS_DIR="$PWD/persistence/migrations"
export REDIS_URL='redis://localhost:6379/15'
```

迁移使用完整 001–058 链与真实角色，保留 checksum；旧架构非空业务库的门禁见[部署](../operations/deployment.md)。

## Python 与数据库

```bash
make test-existing          # 非 postgres marker 集合；仍须检查用例自己的基础设施要求
make lint typecheck
PYTHONPATH=src python3 -m pytest test/test_model_protocol.py test/test_model_dispatch_logging.py test/test_durable_agent_contract.py -q

# 仅在上面的隔离 DSN 已设置后执行
make db-up
make migrate
make test-db
make test-api
PYTHONPATH=src python3 -m pytest test/work_domain -q
```

`test/work_domain/` 的 fixture 自己要求三个真实角色 DSN，部分测试还有 temporal marker；没有 postgres marker 不代表不会使用数据库。标记 temporal 的测试需要 TEMPORAL_HOST；崩溃/撤权测试使用独立 TEMPORAL_TEST_NAMESPACE，不能对正在工作的共享 namespace 执行 kill harness。

聚焦入口：

- `test/test_model_protocol.py`、`test_model_dispatch_logging.py`：OpenAI / Anthropic 多工具和连续轮次、实际 MockTransport 发出体、分类和独立读取预算。
- `test/test_durable_agent_contract.py`：包含 8 个注册 Workflow 的真实 sandbox prepare，防止网络依赖间接导入导致启动失败。
- `test/work_domain/`：Work 基础、Execution 解耦、策略、continuation 和只读 delegation。
- `test/web_persistence/test_work_integration.py`、`test_durable_review_regressions.py`：Work 累计预算、成果来源/采用、通知回执、容量、修订与恢复。
- `test/web_api/test_work_foundation.py`、`test_phase_b_api.py`：命令版本、chat/work snapshot、错误与安全重试。
- `test/web_api/test_workspace_direct_upload.py`、`test_workspace_v41_p*.py` 与对应 persistence 用例：父目录、冲突原因、重复授权、下一轮候选、撤权与版本。

## Web 与浏览器

```bash
make web-install
make ci-web                 # lint / typecheck / build / Vitest

# 隔离 DSN / Redis 配置继续生效；独立目录/端口避免冲突
export WEB_API_PORT=8180
export WEB_DEV_PORT=5273
export FILE_STORE_ROOT=/tmp/hpagent-test-file-store
make e2e
```

Playwright 启动自己的 API 与 Vite（reuseExistingServer=false）。API 使用 Fake Run / Artifact executor；Chromium 真实操作页面、账户和数据库。测试依赖共享 alice / bob，按一个 worker 串行运行。重复使用测试库时，上一轮残留 Work / Run 可能阻塞测试执行器；清理应仅针对已确认的测试库，不能清理业务库。

`web/e2e/manual-repair.spec.ts` 覆盖子目录创建、冲突、上传授权、切页草稿、窄屏 Work 创建/修订/授权；workspace-p1 / p3 与 artifact 覆盖保存、版本、下载及页面跳转。`web/src/components/WorkspacePanel.test.tsx` 覆盖保存成功而授权失败的补救与不重复上传。

## 能力与真实验收

```bash
python scripts/check/mcp-health.py --help
python scripts/check/models.py --help
./scripts/check/gateway-smoke.sh
./scripts/check/release-smoke.sh
make agent-benchmark-check
```

Model / release smoke 和显式 `make agent-benchmark-run` 可能调用真实外部服务，需要有效凭据。模型验收至少覆盖工具首次返回后的第二次请求与多轮结果配对；应从实际 Worker 网络环境复验，并保存脱敏关联证据。快照冻结、MockTransport 或浏览器 Fake executor 的成功不代表真实供应商成功。

最新已通过批次、重叠计数规则、真实 Provider 连接阻碍与 Research SIGKILL 的短租约条件见[实施索引](../implementation/README.md)。Workspace 1k / 10k 规模和原始阶段退出条件见[验收](../implementation/workspace-v4.1/ACCEPTANCE.md)。
