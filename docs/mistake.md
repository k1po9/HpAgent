# Mistakes & Fixes

---

## 1. docker-compose v1 与 Docker Engine 29.x 不兼容

**日期**: 2026-05-19

**错误**:
```
KeyError: 'ContainerConfig'
File "/usr/lib/python3/dist-packages/compose/service.py", line 1579
    container.image_config['ContainerConfig'].get('Volumes') or {}
```

**原因**: `docker-compose` v1 (Python, 1.29.2) 读取镜像 config 时期望 `ContainerConfig` 键，但 Docker Engine 29.x 的镜像 manifest 格式（OCI 规范）已不再包含该字段。这是旧版 Python compose 与新版 Docker Engine 之间的已知兼容性问题。

**解决**: 使用 `docker compose`（v2，Go 插件，无连字符）替代 `docker-compose`（v1，Python）。

```bash
# 错误: docker-compose up -d hindsight
# 正确:
docker compose up -d hindsight
```

**版本信息**:
- docker-compose: 1.29.2 (Python) ← 不兼容
- docker compose: v5.1.3 (Go)    ← 正常
- Docker Engine: 29.4.1

---

## 2. Hindsight 内嵌 pg0 重启数据损坏 → 无限重启循环

**日期**: 2026-05-19

**错误**:
```
WARNING: pg0 data directory exists at /home/hindsight/.pg0 but no PG_VERSION found.
This may indicate data corruption or an incomplete previous shutdown.
API did not become healthy within 300s
```

**原因**: 两个问题叠加导致无限重启:
1. 内嵌 pg0 在容器被 kill 时（模型加载超时触发健康检查失败）未正常关闭，重启后 PG_VERSION 丢失，数据损坏
2. BGE-M3 + bge-reranker-v2-m3 两个模型首次下载需 5-8 分钟，`start_period: 120s` 完全不够，容器在模型加载完成前就被 docker 杀掉

循环: 启动 → 下载模型(慢) → 健康检查超时 → 被 kill → pg0 损坏 → 重启 → 重新下载模型(更慢，因为部分缓存损坏) → 再次超时 → ...

**解决**:
1. 用外部 `pgvector/pgvector:pg16` PostgreSQL 替代内嵌 pg0，数据独立于 Hindsight 容器生命周期
2. `start_period` 从 120s 延长到 600s（10 分钟），retries 从 12 增加到 60
3. 模型缓存挂载到独立 volume(`hindsight-cache:/home/hindsight/.cache`)，避免每次重建容器都重新下载

**关键 diff** (`docker-compose.yaml`):
```yaml
# 新增独立 PostgreSQL
hindsight-postgres:
  image: pgvector/pgvector:pg16
  environment:
    POSTGRES_USER: hindsight
    POSTGRES_PASSWORD: hindsight
    POSTGRES_DB: hindsight

# hindsight 服务改动
hindsight:
  environment:
    - HINDSIGHT_API_DATABASE_URL=postgresql://hindsight:hindsight@hindsight-postgres:5432/hindsight
  volumes:
    - hindsight-cache:/home/hindsight/.cache   # 模型缓存，非 pg 数据
  healthcheck:
    start_period: 600s  # 10 min，容纳首次模型下载
    retries: 60
```

---

## 3. Hindsight 镜像内硬编码 300s 启动超时

**日期**: 2026-05-19

**错误**:
```
❌ API did not become healthy within 300s
```
BGE-m3 和 bge-reranker-v2-m3 两个模型加载本身就需 5-8 分钟，`start_period: 600s` 只解决了 Docker 层面的健康检查，但**镜像内部的 `/app/start-all.sh` 自己还有一层硬编码的 300s 超时**（在 Docker 健康检查之前就会先 kill 进程）。

**原因**: 镜像 `ghcr.io/vectorize-io/hindsight:latest` 的 CMD 是 `/app/start-all.sh`，其内部用一个 for 循环等待 API healthy，硬编码 `API_STARTUP_WAIT_SECONDS=300`（旧版本），不读取任何环境变量。Docker 的 `start_period` 还没开始计时，进程就已被脚本自己 `exit 1`。

**解决**: 
1. cp 镜像内的 `/app/start-all.sh` → `scripts/start-all.sh`，将硬编码改为环境变量：`API_STARTUP_WAIT_SECONDS="${HINDSIGHT_API_STARTUP_WAIT_SECONDS:-600}"`
2. docker-compose 中 bind mount 覆盖原脚本：`./scripts/start-all.sh:/app/start-all.sh:ro`
3. 配合设置 `HINDSIGHT_API_STARTUP_WAIT_SECONDS=600`

**关键配置**:
```yaml
hindsight:
  environment:
    - HINDSIGHT_API_STARTUP_WAIT_SECONDS=600
  volumes:
    - ./scripts/start-all.sh:/app/start-all.sh:ro  # 覆盖镜像内脚本
```
