# 测试

## Python

```bash
make test-existing          # 非 PostgreSQL 测试
make lint typecheck
PYTHONPATH=src python3 -m pytest path/to/test.py
```

PostgreSQL 测试：

```bash
make db-up
make migrate
make test-db
make test-api
```

标记为 `temporal` 的测试需要可访问的 Temporal Server。可靠性与 Worker Recovery 测试可能创建临时进程或容器；不要直接对共享环境运行未经确认的测试。

## Web

```bash
make web-install
make ci-web              # lint + typecheck + build + unit test
make e2e                 # Playwright
```

## 能力检查

```bash
python scripts/check/mcp-health.py --help
python scripts/check/models.py --help
./scripts/check/gateway-smoke.sh
./scripts/check/release-smoke.sh
```

Release Smoke 需要有效凭据和模型 Provider。Model Check 除 Help/Dry 路径外会调用外部服务。

## Benchmark

Benchmark Runner 位于 `scripts/benchmarks/`。Agent Strategy Runner 只有显式选择执行模式后才会调用模型：

```bash
make agent-benchmark-check
make agent-benchmark-run
```

Benchmark 可能需要真实基础设施、模型凭据和较长运行时间，不应当作普通单元测试执行。
