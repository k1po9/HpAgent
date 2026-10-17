# HpAgent

HpAgent 是支持 Web 与 QQ 入口的 AI 助手平台。Main 负责对话，Work 保存持续委托，Run 记录有限执行，Execution 隔离执行尝试。

核心能力包括 PostgreSQL + Outbox 命令接收、Temporal 持久执行、ReAct / Plan-and-Execute、长期记忆、账户级文件与授权、研究、成果版本、通知和渠道投递。Web 提供 AI、空间、任务三个入口；执行、文件、任务和 HTML 成果在对象上下文中查看。

## 开发快速开始

前置条件：Git、Docker Engine 与 Docker Compose v2。完整开发栈使用 Compose，不要求宿主机安装 Python/Node。

1. 首次复制配置，已有 `.env` 时保留原文件：

   ```bash
   cp -n .env.example .env
   ```

2. 设置三个数据库角色密码 `HPAGENT_MIGRATE_PASSWORD`、`HPAGENT_API_PASSWORD`、`HPAGENT_WORKER_PASSWORD`；按 `config/models.yaml` 和 `.env.example` 填写实际 Provider 配置。Secret 只放本地环境，不提交。配置入口见[配置参考](docs/reference/configuration.md)。
3. 启动开发组合；以下地址必须与浏览器实际访问的 origin 一致：

   ```bash
   WEB_PUBLIC_ORIGIN=http://localhost:5173 WEB_COOKIE_SECURE=false docker compose --profile web up -d --build
   docker compose --profile web ps
   curl --fail http://127.0.0.1:8080/health/ready
   docker compose exec -T hpagent python -m orchestration.worker_health
   ```

4. 打开 <http://localhost:5173>，选择注册并创建 Web 用户名/密码（密码8–128字符），成功后自动登录；首次QQ绑定引导可选择稍后处理。当前自助注册默认获得 standard 档、每日50,000 token额度和不展示Prompt；邀请码可提供管理员预置权益。管理员身份绑定工具不创建登录密码，不能替代注册。使用模型前还须核实所选档位与模型配置匹配。

Compose 自动先运行迁移。**已有旧架构业务数据时，先阅读[部署与升级限制](docs/operations/deployment.md)**：054/055/057 有空业务存储门禁，自动迁移不提供历史数据转换。API ready、Worker healthy 与真实模型链验收分别成立。

## 生产部署

使用 `web-prod` 网关组合，按[部署文档](docs/operations/deployment.md)设置生产 Secret、TLS、公共 Origin 和安全 Cookie，并取得匹配的应用PG、File Store与Temporal备份。当前 Compose 的部分应用服务仍挂载宿主源码，发布版本必须包含对应checkout，不能只记录镜像tag。

UI 的 R1–R6 接受不等于生产上线验证；真实模型、Worker、外发、恢复和真机范围须单独验收。[发布检查](scripts/README.md)会明确列出构建、启动和真实请求副作用。

## 配置、开发与测试

| 入口 | 内容 |
| --- | --- |
| [配置参考](docs/reference/configuration.md) | `.env`、`config/config.yaml`、`models.yaml`、Prompt、MCP与搜索配置 |
| [开发环境](docs/development/setup.md) | 宿主Python/Node、Compose与独立端口 |
| [测试指南](docs/development/testing.md) | 单元、PG/API、Temporal、浏览器与真实验收的环境边界 |
| [脚本索引](scripts/README.md) | 公共命令、参数、依赖、副作用、输出与失败行为 |
| [日志指南](docs/operations/logging.md) | `logs.sh` 与结构化关联诊断 |
| [文档导航](docs/README.md) | 使用者、开发者与运维入口 |

```bash
make help
make web-install            # npm ci；宿主Node >=22
make docs-check scripts-test
make ci-web                 # lint、typecheck、Vitest、生产构建
```

数据库fixture会清理业务表，浏览器测试会迁移和写账户数据；执行 `make e2e` 前必须按测试指南配置专用数据库、Redis、文件目录与端口。不要沿用日常业务环境。

## 目录

`src/` 应用/领域/Worker/API；`web/` React/Vite与正式测试；`config/` 运行配置；`persistence/` 迁移与初始化；`test/` 后端测试及fixture；`scripts/` 开发/运维/检查/实验；`docs/` 当前文档及明确标注的历史依据。详细职责见[目录参考](docs/reference/repository-layout.md)。

当前约束从[架构总览](docs/architecture/overview.md)、[Work](docs/architecture/durable-work-v1.md)、[Workspace](docs/architecture/workspace-v4.1.md)和[Web交互](docs/architecture/web-ui.md)进入。历史接受基线与验证范围见[实施索引](docs/implementation/README.md)，仓库外材料恢复见[归档索引](docs/development/repository-archive-index.md)。
