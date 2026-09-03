# File Assistant F3 Version Acceptance

F3 只覆盖不可变派生版本与声明式 Office patch。它不提供任意脚本、原地覆盖、删除、
外部发送或多人协作编辑。

## Migration

```bash
cd /home/hp/workspace/HpAgent_web
PYTHONPATH=src \
APP_DATABASE_URL='postgresql://hpagent_migrate:hpagent_migrate@127.0.0.1:5434/hpagent' \
.venv/bin/python -c 'from persistence.migrate import migrate; migrate()'
```

原理：027 增加 `parent_file_id/version` 并由数据库根据父版本计算版本号；028 冻结 ready
文件的 lineage。两个 migration 均由 runner 记录 checksum，禁止修改历史文件。

## Domain 与 Adapter

```bash
TMPDIR=/tmp .venv/bin/pytest -q -s \
  test/test_file_version_tools.py \
  test/test_file_lineage_migration_contract.py \
  test/test_run_file_workspace.py
```

原理：DOCX literal replace/append、XLSX bounded range write、PPTX single-slide replace 都读取
不可变源文件并生成新文件；公式仅保存，不实现 Excel 计算引擎。先前发布的 Run output 会从
TenantFileStore 重新物化为只读源，因此可继续生成 v3。

## PostgreSQL 与 API lineage

```bash
TMPDIR=/tmp \
APP_DATABASE_URL='postgresql://hpagent_api:hpagent_api@127.0.0.1:5434/hpagent' \
MIGRATION_DATABASE_URL='postgresql://hpagent_migrate:hpagent_migrate@127.0.0.1:5434/hpagent' \
WORKER_DATABASE_URL='postgresql://hpagent_worker:hpagent_worker@127.0.0.1:5434/hpagent' \
.venv/bin/pytest -q -s \
  test/web_persistence/test_file_lineage.py \
  test/web_persistence/test_file_output_persistence.py
```

原理：测试建立 root→v2→v3，证明版本号不能由调用方伪造、ready lineage 不能重写，且
`GET /api/v1/files/{file_id}/lineage` 只在目标 output 已绑定 completed assistant message 后
按 root-first 返回。

## 完整回归

```bash
TMPDIR=/tmp PYTHONPATH=. .venv/bin/pytest -q -s -m 'not postgres and not temporal'
.venv/bin/ruff check src test
git diff --check
docker compose config --quiet
```

必须使用 Linux `TMPDIR`；Windows DrvFS 无法可靠表达 `0400/0700` 权限位，会造成文件隔离
测试的环境假失败。

## 回滚

```bash
export WEB_FILE_TRANSFORM_ENABLED=false
docker compose --profile agent up -d hpagent
```

关闭 transform 后版本记录仍可查询和下载。不要回滚或编辑 027/028；数据库新增列保持向后
兼容，旧文件自动视为无父节点的 version 1。
