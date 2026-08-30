# Docker 镜像离线部署

本流程在联网构建机完成第三方镜像拉取和 HpAgent 镜像构建；运行机只执行 `docker load`，随后禁止 Compose 构建或拉取镜像。镜像归档不包含 PostgreSQL、Redis、Temporal、Hindsight 等 volume 数据，也不会修改或删除 volume。

## 镜像清单

`web-prod` 生产组合需要以下镜像：

| 类型 | Compose 服务 | 镜像 |
| --- | --- | --- |
| 第三方 | `app-postgres`, `temporal-postgres` | `postgres:16-alpine` |
| 第三方 | `redis` | `redis:7-alpine` |
| 第三方 | `temporal` | `temporalio/auto-setup:1.26.2` |
| 第三方 | `hindsight-postgres` | `pgvector/pgvector:pg16` |
| 第三方 | `hindsight` | `ghcr.io/vectorize-io/hindsight:0.6.1` |
| 项目构建 | `hpagent-migrate` | `hpagent/hpagent-migrate:offline` |
| 项目构建 | `hpagent` | `hpagent/hpagent:offline` |
| 项目构建 | `hpagent-api` | `hpagent/hpagent-api:offline` |
| 项目构建 | `web-gateway` | `hpagent/web-gateway:offline` |

其他 profile 的镜像：`web` 使用 `node:22-bookworm-slim`，`qq` 使用 `mlikiowa/napcat-docker:latest`，`tools` 使用 `temporalio/ui:2.34.0`。将这些 profile 作为脚本的后续参数即可一并打包。镜像名可分别通过 `HPAGENT_MIGRATE_IMAGE`、`HPAGENT_IMAGE`、`HPAGENT_API_IMAGE`、`WEB_GATEWAY_IMAGE` 覆盖；构建机与运行机必须使用相同值。

## 构建机

默认导出 `web-prod` 所需的全部镜像：

```bash
./scripts/docker-offline-export.sh dist/hpagent-offline-images.tar
```

同时包含 QQ 和 Temporal UI：

```bash
./scripts/docker-offline-export.sh dist/hpagent-offline-images.tar web-prod qq tools
```

脚本会依次拉取第三方镜像、构建项目镜像、从 Compose 配置解析并校验镜像清单，然后生成：

- `hpagent-offline-images.tar`
- `hpagent-offline-images.tar.manifest.txt`
- `hpagent-offline-images.tar.sha256`

将归档、校验文件、相同版本的代码和生产环境变量传到运行机。Manifest 用于审计，不是加载脚本的必需输入。

## 运行机

在项目根目录执行：

```bash
./scripts/docker-offline-load.sh /path/to/hpagent-offline-images.tar
```

若归档旁存在同名 `.sha256` 文件，脚本会先校验；加载后，它会确认当前 `web-prod` Compose 所需镜像全部存在。然后用明确禁止构建和拉取的命令启动：

```bash
docker compose --profile web-prod up -d --no-build --pull never
```

若归档包含多个 profile，加载和启动时传入同样的 profile：

```bash
./scripts/docker-offline-load.sh /path/to/hpagent-offline-images.tar web-prod qq tools
docker compose --profile web-prod --profile qq --profile tools up -d --no-build --pull never
```

公网流量仍应保持 `nginx -> Tailscale -> web-gateway:80`。Compose 中 PostgreSQL、Redis、Temporal、Hindsight 的宿主机端口仅绑定 `127.0.0.1`；不要将这些内部服务或 `hpagent-api` 设为公网入口。

## 数据边界

`docker save` 只导出镜像，不导出命名 volume 或 `.data`。需要迁移已有服务时，应在单独的维护窗口使用 `pg_dump` 等数据备份方案。此离线镜像流程不会执行 `docker compose down -v` 或任何 volume 删除操作。
