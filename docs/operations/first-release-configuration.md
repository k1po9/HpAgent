# 首次五人 Beta 配置入口

2026-10-18。依据用户填写表、当日对照检视及本轮明确决定实施；本文替代旧的待确认表。状态为**代码/模板已调整，未部署、未完成运行验收**。允许公开注册，面向约五人使用及面试展示，本轮不增加注册或 API 限速。

## 启用范围与发布

Web、NapCat、Research、长期记忆、上传和受控 Office→PDF 转换开启。图像链为空，没有发现现有图像生成工具或 Web 操作入口；不为此更改文件上传类型支持。DeepSeek 无启用 provider/模型引用，仅保留兼容变量入口；生产 Web Host Shell 关闭。

当前源码选择 SiliconFlow 文本链：fast 引用 SILICONFLOW_HIGHSPEED_MODEL，chat 引用 SILICONFLOW_STANDARD_MODEL，reasoning 引用 SILICONFLOW_FLAGSHIP_MODEL，保持现有超时和重试；reasoning 不自动被业务选用。Hindsight 的 LLM 仍独立使用 MiniMax 配置。完整映射与安全检查见[模型分工表](../reference/model-routing.md)。SiliconFlow 同时供 Embedding、Rerank 及 Hindsight 检索使用，必须提供相应凭据。原生请求参数尚待按[方案](../design/model-native-parameters-plan.md)核实，不视为已兼容。Alibaba 无其他启用引用，已删除配置。Hindsight LLM 的 provider/base URL/model/key 仍须单独填写。

只保留一套常驻 Beta。首次新建空卷，旧开发数据保留，不导入、不删除。独立 Beta 目录、干净 Git 提交、镜像 ID、配置版本及迁移状态共同标记发布版本；保留源码挂载。Python 修改需重启对应 API/Worker；依赖、前端、Document 及其他未挂载内容变化按需重建。禁止在日常开发目录边编辑边对外服务。

建议 project 名 `hpagent-beta`；最终目录待填写。卷实际按 project 前缀生成，例如 `hpagent-beta_app-pgdata`，部署前用 `docker volume inspect` 核实。测试只用临时隔离目录、端口、卷及假凭据；仅换 project 名不能隔离固定端口和 `.data`。

## 配置准备

复制 [.env.example](../../.env.example) 到独立 Beta 目录的私有 `.env`，权限限制为运维本人。不要将其提交 Git。示例仍保留本地认证默认值；Beta 必须合并 [docker-compose.beta.yaml](../../docker-compose.beta.yaml)：

```bash
# 以下仅为后续部署操作说明，本轮没有执行 up/stop/迁移。
export COMPOSE_PROJECT_NAME=hpagent-beta
export COMPOSE_FILE=docker-compose.yaml:docker-compose.beta.yaml
# 补齐私有 .env 后只做语法检查；勿公开完整 config 输出。
docker compose --profile web-prod --profile qq config --quiet
```

Beta overlay 固定 `production`、`https://hpagent.cloud`、Secure Cookie、Host Shell=false、外部服务直连；API 保持 Fake=false。实际 `.env` 中历史的 128MiB/10 个/transform=false 会覆盖基础 Compose 默认值，迁移时要按下表明确更新。

| 项目 | Beta 值/处理 |
| --- | --- |
| 单文件 / 每消息 | `FILE_MAX_BYTES=33554432` / `FILE_MAX_COUNT_PER_MESSAGE=5` |
| 普通直读 / 清理轮询 | `FILE_DIRECT_READ_MAX_BYTES=1048576` / `FILE_CLEANUP_INTERVAL_SECONDS=300` |
| 文件开关 | upload=true、transform=true、shell=false、预算 enforce |
| Work 活跃 Run | 账户3 / 全局16，API、Worker均透传 |
| model、tool、fetch | 每种分别 GLOBAL=8 / INTERACTIVE_RESERVED=3 / ACCOUNT=2 |
| Lease / 成果读取超时 | 沿用900秒 / 90秒 |
| 渠道 | `channels.enabled: [napcat]`；Web 独立服务，官方QQ未启用 |

Work 上限只计 `source_kind=work` 且 queued/running/cancelling 的 Run。资源票据按 model、tool、fetch **分别计数**，不是整机共8个并发，也不是每个活跃 Work 都在调用模型。交互保留3使各资源后台总上限5；账户2且有交互保留时，单账户后台该资源上限1。调度、预算预留/结算及 UTC 日期语义未改。

所有主要 Beta 服务都有 `BETA_<服务名>_CPUS` / `_MEMORY` 必填入口（连字符改为下划线，见示例）。这包括三套数据库、Redis、Temporal、Hindsight、搜索、Gotenberg、迁移、主/文档Worker、API、网关及NapCat。填写之前 overlay 会报缺失错误；没有提供冒充安全预算的默认值。先确定 WSL/Docker 总预算并给 Windows 日常使用留余量，再分配各服务，压测后确认。容器预算不是每任务隔离；每任务1核/1GiB/120秒仅为评估目标。没有新增调度或任务容器系统。

## 密码与账户

`TEMPORAL_DB_PASSWORD` 同时用于 Temporal PostgreSQL 与 Temporal；`HINDSIGHT_DB_PASSWORD` 同时用于 Hindsight PostgreSQL 与连接 DSN。Compose 缺失或空值时报明确错误（开发也必须填写）。两项独立生成和保管；Hindsight 密码应使用 URL 安全的随机十六进制，避免未经编码的 DSN 特殊字符。应用数据库三角色密码及四类认证签名材料仍需独立配置；此轮没有生成/轮换任何实际密钥。**已初始化数据库不能仅改变量**，须另行受控变更库内角色密码及客户端；本轮未接触运行库。

A/B/C 是套餐名称，分别5000/50000/100000 token每天，均使用现有 `standard` 模型 tier，没有等级继承。自助注册默认 A，Prompt 可见性 `none`；邀请码 CLI 默认也改为 `none`，只接受普通 `standard`，B/C复用现有邀请码：

```bash
python scripts/operations/create-registration-invite.py --profile standard --daily-token-limit 50000 --prompt-visibility none
python scripts/operations/create-registration-invite.py --profile standard --daily-token-limit 100000 --prompt-visibility none
```

上述命令后续需要运维迁移凭据，实际执行会写库并输出一次性邀请码，本轮未执行。普通邀请不得授予 owner/管理员；底层邀请服务仍是受控数据库运维能力，不能当成面向用户的任意 profile API。签发前复核 tier、额度、Prompt 可见性；历史邀请码保持原权限，不自动撤销或修改，部署前应人工审查。

D 的额度目标300000，模型访问权限另行记录/确认。`owner` 当前可访问全部模型 tier，但不是管理员后台或角色系统。受控 `bootstrap_identity` 新建账户仍授予 owner/full_safe、无每日上限且不创建 Web 密码，仅供身份恢复/首位维护者处理；普通 Web 注册、邀请码注册、QQ绑定不得借此批量开户。已有账户路径不会被默认额度修改自动追溯变更。D的tier/Prompt/额度须通过受控运维事务核实，不新增管理API。各档更高 Prompt 可见性尚待用户选择，默认不额外授权。

日额度仍按 UTC 日历日（北京时间08:00），不改预算预留/结算归属。5000是模型输入输出综合额度，不能保证一次多步骤 Research 必然完成。详见[账户治理](account-governance.md)。

## 文件、网关与代理信任

API 对上传单文件字节数和消息附件数量执行限额；Worker和Document读取相同单文件上限。Worker普通 `read_file` 实际消费1MiB直读阈值，超过阈值报错，可使用已有的格式专用按页/范围读取。API没有直接读取工具，不把变量透传冒充 API 执行直读限制。现有镜像MarkItDown缺少DOCX快速文本转换可选依赖，专用DOCX按段落/表格读取已通过测试，普通DOCX全文快读不列为已验收。Document自动归一化路由目前因取消传播边界未完成而关闭，此轮不重开。Document服务保留，不承诺所有文件自动进入Docling。清理线程每300秒检查现有的Run清理队列，不等于24小时保留策略。

受控转换沿用已有 Gotenberg Provider，开启 transform 时注册 `convert_file_to_pdf`，只接受当前Run内 DOCX/XLSX/PPTX 并发布PDF，输出受字节限制；关闭时不可调用。不执行Host Shell。

前端每个文件单独发原始 PUT，不是将5个文件打包。仓库网关 `client_max_body_size 32m` 与32MiB边界一致，不设160/180MiB。宝塔站点也需 `client_max_body_size 32m;`。其他 JSON 请求保留 API 的独立限制。

本地网关 `/api/`（覆盖 Work SSE）、`/api/v1/runs/`（Run SSE）均关闭缓冲/缓存，读写超时3600秒、HTTP/1.1并清空Connection。云端宝塔在相应代理 location 同样设置：

```nginx
client_max_body_size 32m;
proxy_http_version 1.1;
proxy_set_header Host $host;
proxy_set_header X-Forwarded-Proto $scheme;
proxy_set_header X-Forwarded-For $remote_addr;
proxy_set_header Forwarded "";
proxy_set_header Connection "";
proxy_buffering off;
proxy_cache off;
proxy_read_timeout 3600s;
proxy_send_timeout 3600s;
```

只在维护者检查现有站点后合并，本文不是已应用的云端配置。外层 HTTPS 请求的 scheme=https；本地HTTP跳转网关重建自己的代理头，内层 scheme=http。API 显式不解析代理头，认证 Cookie 与来源校验使用固定公开Origin与Secure设置，因此不依赖内层 scheme 推断公网协议。未引入 RealIP 或任意来源可信代理，不用外部 X-Forwarded-For 做安全授权；未来若需真实客户端IP，要先配置精确可信代理来源。

网关默认绑定127.0.0.1；实际启用 Tailscale 入口时，`WEB_GATEWAY_BIND_ADDRESS` 须填经核实属于 Docker宿主的受控地址，或配合防火墙/Tailnet ACL 明确限制入口。不能把未核实的 `100.97.222.24` 直接当成 WSL可绑定地址。确认Windows/WSL/Docker拓扑、重启后路由、防火墙、云到本地连通、域名解析、证书续期及电脑休眠影响。静态代码不能证明这些已通过。

## 工具、镜像与权限

`mcp.auto_connect=false` 且现有每个 server 均 `disabled=true`；`skills.enabled=false`。现有远程候选为 tavily-mcp、amap-maps、bazi-mcp、howtocook-mcp、12306-mcp；stdio stock-sdk 和 Skill pdf-processing 均不启用。这些只是待审核候选，没有被认定为只读或已授权。后续逐个确认业务用途、实际工具读写/执行能力、端点与数据去向，才可启用对应配置；`side_effect_class` 不是权限沙箱。没有新增通用白名单平台。

核查 `setup_tools`、SandboxManager 及执行注册路径：Host Bash默认关闭，文件模式下普通本地工具/MCP/Skills不注册；Web只开放当前Run文件作用域及既有Work工具。配置保留 native_tools，但不能据此断言所有通道都有Bash。Research搜索/抓取是独立路径，不依赖外部MCP。

主Worker/API/Document镜像当前未声明USER，默认root；保持现有卷读写兼容性，本轮没有机械切换USER，也没有宣称非特权隔离已完成。未配置 privileged、Docker socket 或宿主根目录挂载；主Worker仍写挂载的src/tools/.data，API源码只读，Document只读File Store并写独立临时卷。仅受控运维人员可写Beta源码与配置；不承诺防御任意代码执行的OS级隔离。跨账户路径与文件操作有现有测试，实际卷ownership、重建后写入需部署验收。

根、src（Document独立context）、web构建排除规则覆盖环境密钥和运行数据；配置通过只读挂载注入，不在镜像中复制生产 `.env`。MCP注释中不必要的疑似凭据明文已移除。历史中若有有效凭据仍由所有者撤销/轮换，本轮未尝试验证或自动轮换。

## 操作约定与待填写

- 文件总10GiB/账户3GiB、Run/Document各3GiB、结束后24小时清理仅为人工容量目标；总量优先，满时先人工暂停接收文件。没有实现累计配额/24小时自动清理，不删除运行任务材料。
- 记忆90天自动过期未实现；`memory_retention_worker` 是写入Outbox消费者。当前适配器没有用户记忆删除/注销清理API，也没有可承诺的敏感信息自动过滤。删除请求需维护者核实账户对应bank与上游Hindsight版本实际删除能力，暂停相关写入防止再写入，再受控处理并核验；本轮不调用上游、不删历史、不承诺一键删除。
- 应用日志按日轮转保留14个历史文件，JSON文件跟随LOG_LEVEL；Docker日志补齐有界轮转。14天/总2GiB是人工预算，单日文件及总量硬限制、自动容量告警、独立备用通知尚未实现。使用现有日志/observability-viewer，人工检查磁盘与服务健康。
- RPO48小时/RTO24小时、每日低峰完整备份、30天保留、云端15GiB为目标，需容量核实与隔离恢复演练；完整步骤见[备份恢复](backup-restore.md)。没有建立自动调度、加密异机同步或告警闭环。

仍需填写：Beta绝对目录与上线时段；WSL/Docker总CPU/内存和各服务预算、磁盘空闲；网关绑定与Tailscale地址归属/ACL；独立密码与签名材料的保管/注入状态；MiniMax/SiliconFlow/Hindsight实际模型ID及凭据；NapCat账号/连接与已验证固定镜像tag/digest；远程MCP/Skills白名单（暂全关）；B/C/D Prompt可见性和D模型访问tier；项目外及异机备份路径、加密材料保管位置、独立告警渠道。不要填写设备ID/产品ID或在本文粘贴密钥。

## 验收边界

此次只执行静态和隔离测试，不执行发布smoke、真实模型、QQ外发、部署、云端修改或真实数据清理。实际还要在维护者安排的验收窗口检查 HTTPS注册/登录/退出/CSRF、32MiB及超限上传、5个附件、Run/Work SSE更新与断线恢复、Research/记忆/受控转换、QQ绑定及收发、取消与故障恢复、容量与卷权限、日志增长、完整备份隔离恢复。未演练前不标记RPO/RTO或首发验收通过。

## 本轮验证记录（2026-10-18）

- 114项相关测试通过：文件读写/权限/转换工具路由、单文件32MiB与超出1字节的chunked上传、注册及邀请码权益、文件谱系/直接上传、配置校验、脚本安全与冷备失败/成功路径。数据库测试使用本轮独立内部Docker网络和tmpfs PostgreSQL，不挂载业务卷，测试后移除。
- Compose仅用假凭据和假预算、`--env-file /dev/null`验证合并、两套内部密码消费者一致、API/Worker容量及文件参数一致、服务限制及缺失必填值错误；没有读取/改写生产.env，没有以假预算启动Beta。
- 网关镜像在无网络临时容器中运行`nginx -t`通过；修改Python文件的Ruff检查、备份Shell语法、脚本安全测试通过。文档检查33个Markdown文件，0处本地链接错误。
- 未构建新发布镜像，未执行完整真实冷备/恢复、发布smoke、模型/QQ/云端/Tailscale/证书/真实卷权限与资源负载验收。归档测试使用假Docker和临时数据，不表示RPO/RTO达成。
