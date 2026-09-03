# File Assistant F2 Runtime Acceptance

本手册验收 F2 的不可变输出闭环：Durable Tool → Run `outputs/` → TenantFileStore →
`stored_files/run_files` → completed assistant message → authenticated download。

## 1. 前置条件

```bash
cd /home/hp/workspace/HpAgent_web
export APP_DATABASE_URL='postgresql://hpagent_api:hpagent_api@127.0.0.1:5434/hpagent'
export MIGRATION_DATABASE_URL='postgresql://hpagent_migrate:hpagent_migrate@127.0.0.1:5434/hpagent'
export WORKER_DATABASE_URL='postgresql://hpagent_worker:hpagent_worker@127.0.0.1:5434/hpagent'
```

原理：API、migration、Worker 使用不同数据库角色，测试会验证 Worker 能发布输出而 API
不能伪造未绑定结果。

## 2. 静态与单元验收

```bash
.venv/bin/ruff check src test/test_file_output.py test/web_persistence/test_file_output_persistence.py
git diff --check
.venv/bin/pytest -q -s test/test_file_output.py
```

原理：writer 只接受有界声明式 DOCX/XLSX/PPTX 数据；不接受脚本、宿主路径或任意模板。

## 3. PostgreSQL 幂等与可见性

```bash
APP_DATABASE_URL="$APP_DATABASE_URL" \
MIGRATION_DATABASE_URL="$MIGRATION_DATABASE_URL" \
WORKER_DATABASE_URL="$WORKER_DATABASE_URL" \
.venv/bin/pytest -q -s test/web_persistence/test_file_output_persistence.py
```

原理：相同 `operation_id` 必须返回相同 `file_id`；Run 完成前 output 即使已经物理发布也
不可下载，完成事务会把所有 Run output 绑定到 assistant message。

## 4. Gotenberg

```bash
docker compose pull gotenberg
docker compose up -d gotenberg
docker compose ps gotenberg
curl --fail http://127.0.0.1:3001/health
```

原理：LibreOffice/Chromium 生命周期由常驻 Gotenberg 管理，Agent Worker 仅通过内部 HTTP
调用。转换响应采用流式、有最大字节数的写入，超限删除 `.part`。

用真实 DOCX 验证转换：

```bash
tmp_dir="$(mktemp -d)"
cp test/fixtures/files/sample.docx "$tmp_dir/input.docx"
curl --fail --output "$tmp_dir/output.pdf" \
  --form 'files=@'"$tmp_dir/input.docx" \
  http://127.0.0.1:3001/forms/libreoffice/convert
file "$tmp_dir/output.pdf"
```

若仓库没有该 fixture，可使用任意不含敏感信息的本地 DOCX 替换路径。测试完成后删除
临时目录。

## 5. 启用正式写工具

```bash
export DURABLE_AGENT_ENABLED=true
export WEB_FILE_UPLOAD_ENABLED=true
export WEB_FILE_TRANSFORM_ENABLED=true
export GOTENBERG_URL=http://gotenberg:3000
docker compose --profile agent up -d --build hpagent gotenberg
```

原理：写工具必须同时满足 Durable Agent、文件上传能力和 Gotenberg 配置。缺少任一项时
Worker 启动失败关闭；`WEB_FILE_SHELL_ENABLED` 保持 `false`。

## 6. 回归

```bash
.venv/bin/pytest -q -m 'not postgres and not temporal'
APP_DATABASE_URL="$APP_DATABASE_URL" \
MIGRATION_DATABASE_URL="$MIGRATION_DATABASE_URL" \
WORKER_DATABASE_URL="$WORKER_DATABASE_URL" \
.venv/bin/pytest -q -m postgres test/web_persistence
```

验收通过条件：输入哈希保持不变；失败不产生可下载文件；重试不重复登记或扣减预算；
Trace 只包含大小、状态、operation 后缀等白名单元数据；无附件 Web 与 QQ 回归通过。

## 7. 回滚

```bash
export WEB_FILE_TRANSFORM_ENABLED=false
docker compose --profile agent up -d hpagent
```

原理：关闭入口不会删除已发布文件或数据库记录。F2 复用 migration 017 已有 output schema，
没有新增 migration；025/026 继续属于 Document Worker，禁止修改其已记录 checksum。
