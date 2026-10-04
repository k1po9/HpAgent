# 产品能力—入口—API—owner—场景—证据—结果矩阵

范围依据当前路由/组件/后台代码、Workspace v4.1 行为约束以及已落地的 Durable Work Phase 1–5 实现。旧 `docs/reference/api.md` 仍含 Research Task 描述；当前 `app.py` 的 `/works`、Work requirement 和统一 Run 才是本次路由/owner 权威，不恢复旧 Task 表单或 legacy scheduler。设计目标、历史报告和 API 包装方法均不作为本轮运行通过证据。

状态：**通过**仅限场景列的实际断言；**失败**有实际复现；**未验证（阻断）**没有完成成果/缺环境/权限；**无 Web 入口**是当前没有对应交互；**不属于 Web 范围**是 QQ 专属或内部/运维能力。覆盖状态和执行结果分别说明，避免一个总标签掩盖部分覆盖。

证据编号及文件见 [evidence.md](/home/hp/workspace/HpAgent_web/artifacts/product-acceptance/2026-10-04/evidence.md)。API 路径均省略 `/api/v1` 前缀，`/auth` 除外。UI 证据是本轮真实 Chromium；“代码”证据仅支持入口存在/缺失结论。

修复后补验的 R01–R09 见 [repair-results.md](/home/hp/workspace/HpAgent_web/artifacts/product-acceptance/2026-10-04/repair-results.md)。以下仅更新有新证据的场景，模型最终成果仍保留原阻断结论。

| 产品能力 | 前端入口/覆盖边界 | API | 后台 owner | 验收场景 | 证据 | 结果 |
|---|---|---|---|---|---|---|
| 注册、登录 | LoginForm；确认密码/可选邀请码 | `/auth/register`、`/auth/login`、`/me` | Account Registration/Auth | 新 A/B 注册、真实 Cookie 登录 | E01/E08 | 通过；邀请码 provision 未验证 |
| 会话恢复、退出 | App 会话门；左下退出 | `/me`、`/auth/logout` | Auth/Session | 刷新后登录；B 退出 401，A 保持独立 | E08/E12 | 通过；自然过期/停用未验证 |
| 账号模型资格/额度设置 | 无自助 entitlement 设置页；Run 有 tokens 摘要 | `/me`、模型观察接口；治理主要为内部服务 | Account Entitlement、ModelBudgetCoordinator | 默认账号访问边界；管理员配置没有操作 | E03/E08/代码 | 运维设置不属于 Web 范围；完整额度治理 UI 未覆盖 |
| 连接错误/恢复页 | App 有错误和重试分支 | `/me` | App/Auth | 未人为断网；不伪造响应 | 代码 | 未验证 |
| 对话新建/列表 | 左上新建、左侧列表；空页面错误可关闭 | POST/GET `/conversations` | Conversation CommandService/Queries | localhost 创建；无对话账号错误 Origin 403 提示 | E01/E04/R03 | 修复后 localhost 通过 F01/F02；旧 IP 不再作为允许写入的 Origin |
| 快速切换 | 左侧对话按钮 | `/conversations/{id}`、`/messages` | Workbench + Conversation Queries | A→B→A 最终选 A，消息符合 A | E04 | 通过；本轮未制造受控延迟，历史回归单测另列 |
| 刷新恢复 | App 自动恢复最新对话 | `/me`、列表、详情、消息 | Workbench/Queries | 刷新持久化消息/附件；选回旧 A | E04/E12 | 通过；刷新不保留此前旧对话选择 |
| 对话改名 | 只有 API wrapper，无组件调用入口 | PATCH `/conversations/{id}` | Conversation Commands | 静态核对 sidebar 没有改名操作 | 代码 resources/Sidebar | 无 Web 入口 |
| 对话列表翻页 | sidebar 只加载首批，未见“更多对话” | GET `/conversations?cursor=…` | Conversation Queries | 当前样本少，静态检查下一页入口 | 代码 | 无 Web 入口；超过首批场景未验证 |
| 历史消息翻页 | ChatPane “加载更早的消息” | `/conversations/{id}/messages` | Message Queries | 未生成长历史样本 | 代码 | 未验证；历史 Fake 测试不替代此项 |
| 真实 ReAct 聊天成果 | 底部输入/发送；状态条 | POST `/conversations/{id}/messages`、GET Run、SSE | Main Agent → Temporal AgentLifecycle/React → RunLifecycle | 单次短输入，真实决策结果 | E03 | 失败 F04：model_unavailable，无 completed 助手成果 |
| 计划执行成果 | 执行模式下拉“计划执行” | 同发送接口 `agent_strategy` | PlanExecute Workflow/Agent Activities | 最终计划步骤及成果 | E02/代码 | 未验证（阻断：真实模型） |
| queued 停止 | 状态条/输入区停止按钮 | POST `/runs/{id}/cancel` | RunLifecycle/Outbox | 小输入立即取消，0 模型请求；页面终态/刷新 | E06/R05 | 修复后 API/UI cancelled 通过；不以按钮变灰代替终态 |
| running 模型/工具停止 | 同停止按钮 | cancel、Run、Trace | Temporal cancellation/Agent Activities | 实际工具退出和停止，不能只到 stopping | E06/代码 | 未验证（无已启动成功执行样本） |
| 失败重试 | 状态条安全失败原因/“重试” | POST `/runs/{id}/retry` | Conversation/Run admission | 安全提示与重试规则组件回归；不重复发起超时模型 | E03/代码/R01 | 提示代码已修复；新失败 UI 与真实重试成果未验证 |
| 聊天附件上传/绑定 | 输入区附件、已就绪、消息链接 | `/conversations/{id}/uploads`、PUT upload、POST messages | FileService/Immutable Store/Run bindings | 24-byte 文件上传、消息绑定、queued 取消 | E06 | 通过；Agent 真读取未验证 |
| 已有文件选择 | “选择已有文件” | `/conversations/{id}/file-candidates` | FileService/Conversation binding | 仅观察入口，未发送复用样本 | E02/代码 | 未验证 |
| Workspace 直接上传 | “上传到 Workspace” | `/workspace/uploads`、PUT upload、`/workspace/files` | FileService/Workspace Catalog/TenantFileStore | 43-byte 上传，不新建 Message；下载一致 | E05 | 初始失败 F03；最小修复后通过 |
| 消息文件保存长期入口 | 消息“保存到 Workspace” | POST `/workspace/files` | WorkspaceSaveService/Catalog | chat-input 保存，再移除新 entry | E10 | 通过；生成输出保存未验证 |
| 文件下载内容一致 | entry/消息/v1 下载链接 | `/files/{id}/content` | FileService/TenantFileStore | 浏览器实际下载，文本/hash 与 source 相同 | E05 | 通过；Office/大文件未验证 |
| 原始 File 删除/lineage | 未见用户交互；deleteFile wrapper 不算入口 | DELETE `/files/{id}`、GET `/lineage` | File lifecycle/lineage | 与移除 Workspace entry 区分 | 代码 | 无 Web 入口 |
| 目录创建/移除 | Workspace 名称/目录按钮/影响预览 | `/workspace/directories`、节点 impact/DELETE | Workspace Catalog | 新测试空目录创建、预览、移除 | E10 | 通过；非空目录/深树未验证 |
| entry 改名/移动 | 名称、目标目录、影响预览与确认 | 节点 impact、PATCH `/workspace/nodes/{id}` | Workspace mutation kernel | 改名保持 node/file ID | E05 | 改名通过；跨目录移动未验证 |
| 长期文件搜索 | 名称/MIME/来源/Work/Run/日期/摘要 | `/workspace/search` | WorkspaceSearchService | 改名后按名称找到文件 | E05 | 名称通过；其余过滤/分页未验证 |
| 空间/保留解释 | 文件选择后来源、保留与 bytes | `/workspace/space`、文件 retention | Retention/GC catalog | 去 entry 后仍 67 bytes；消息/Run 引用保留 24 bytes | E10 | 通过；真实物理 GC 不属于本轮 UI 验收 |
| entry 操作来源追踪 | 有来源/版本显示，无独立 operation trace 入口 | `/workspace/nodes/{id}/trace` | Workspace Catalog/operations | 没有 trace 组件调用 | 代码 | 部分来源展示通过；完整 trace 无 Web 入口 |
| 新对话默认不授权长期文件 | 面板显示 Agent 授权数 | Conversation resources | ResourcePolicy/ResourceGrantService | 新 B 对话无长期 grants；所有者可浏览 | E05/E08 | 通过（静态授权状态）；Agent 拒读未验证 |
| 读取/修改授权入口 | 授权当前对话读取文件/目录、修改文件 | POST `/conversations/{id}/resources` | ResourceGrantService | 两条读取规则生成后全部撤销 | E05 | 读取授权/静止撤销通过；修改/递归运行未验证 |
| create_child/delete_entry 权限 | 无独立按钮或通用权限选择 | resources 的 operation 字段 | ResourcePolicy | API 支持不能冒充 UI | 代码 | 无 Web 入口 |
| 运行中撤销最后读取权限 | 撤销按钮及停止状态文案 | DELETE resource、Run cancel | ResourcePolicy + Outbox + Temporal | 已实际选择/读取后撤权、确认工具退出 | E05/代码 | 未验证（阻断：真实模型/工具样本） |
| 附件撤销可用性 | “撤销附件可用性” | DELETE `/conversations/{id}/attachments/{file_id}` | Resource bindings/RunLifecycle | 新测试附件可用 false，历史消息引用保留 | E10 | 通过；运行中撤销停止未验证 |
| Run 固定候选/读取状态 | 活跃 Run 可选资料，版本 Run ID 独立保留 | `/runs/{id}/resources`、Run workspace search | Resource snapshot/RunFileWorkspace | 停止后空状态；延迟响应/分页切换、queued→running | E06/R01/R05 | F07 修复后通过；真实固定/读取状态未验证 |
| v1 可更新历史升级 | 选 entry→启用版本历史 | 节点 `/upgrade`、`/versions` | Workspace version kernel | immutable 转 destination，v1/hash 同对象 | E05 | 通过 |
| v2 提交/CAS/冲突另存 | 已发布 Run 输出选择、提交新版本 | POST 节点 `/versions`、Run published-files | OutputPublisher + Workspace CAS | 必须用真实已发布输出，验证 v1/v2 | E05/代码 | 未验证（无真实生成输出）；入口存在但偏工程化 |
| Artifact 生成/预览/编辑/版本 | 完成助手消息生成、ArtifactPanel/预览 | Message artifacts、Artifact versions、version detail | Artifact service + Artifact-build Work/Run | 构建成功、实际 iframe、v2 保留 v1 | 代码 | 未验证（阻断：没有 completed 助手消息/模型） |
| Work 自然语言接收/修订 | 聊天 Main Agent 的 accept_work/revise_work；无独立表单 | `/works`、`/revisions`；Main tools 同命令服务 | WorkCommandService/Main Agent | 显式委托、持久化 ID、不把叙述当接受 | 代码 application/main_agent | 未验证（阻断：Main 决策超时）；存在聊天入口 |
| Work 列表/控制 | 持续工作面板 | Work GET、pause/resume/stop | WorkCommandService/WorkScheduleService | API 创建未来测试 Work，真实 UI 暂停/恢复/停止；终态文案 | E07/R04 | 通过，F10 文案已修复（API fixture 不计 UI 创建通过） |
| Work 后台提醒最终履约 | 控制/投递状态可看；正文仅通过 API 读取 | Work Runs/events、`/notifications` | Work schedule → deterministic reminder → delivery/completion | 独立真实 Run succeeded、completed、回执、测试标记 | E07/E12 | 通过（API 创建、无模型、仅账户 inbox） |
| Work Generic 最终成果/恢复 | 聊天委托；Work 成果卡/接受按钮 | Work/Run/artifact/accept-result | WorkAgent、WorkCompletionPolicy | 真正成果、要求 revision、接受凭据；跨重启恢复 | 代码 | 未验证（阻断：模型）；没有重启服务做恢复测试 |
| Work 续接/预算/成果接受 | 续接对话、预算翻倍、成果接受等条件按钮 | link、budget、accept-result | Work integration/governance | 本轮无适用成果/耗尽预算 | 代码 WorkPanel | 未验证；有条件 UI 不算通过 |
| Work 实时事件 | 自动订阅，无单独入口 | `/works/{id}/events/stream`、JSON `/events` | ProtocolMiddleware/Work events | 200/SSE event1、after=1续读event2；JSON200 | E07/R02/R04 | F06 修复后通过；完整 UI 自动重连故障注入未验证 |
| 收件箱正文/通知目标配置 | Work 仅显示投递结果，无正文收件箱/目标配置页 | `/notifications`、notification-targets | Delivery service | reminder 内容可在 API 验证，UI 看不到完整通知列表 | E07/代码 | 完整收件箱/目标配置无 Web 入口 |
| Work 输入/资源管理 | 无通用 Work 输入/授权面板；Conversation 授权不能替代 | `/works/{id}/inputs`、`/resources` | Work integration/ResourcePolicy | 当前 UI 未调用这些操作 | 代码 | 无 Web 入口；Main 侧完整支持未验证 |
| Research 接受与真实执行 | 聊天 Main 委托；Research 输出折叠区 | Work capability research_report；Research Run endpoints | Work + ResearchReportWorkflow/Research Activities | 查询、获取、Evidence、Report、发布/required 保存 | 代码/E02/E03 | 未验证（阻断：模型；没有已完成测试 Research） |
| Research 报告/文件查看保存 | ResearchOutputs 按 Work→Run 加载 report、下载/保存；加载/空状态说明 | Work runs、Run `/research/report`、published-files | ResearchQueryService/OutputPublisher/WorkspaceSave | 无报告样本；真实空列表有说明 | E02/E12/R03/R07 | F08 空状态修复通过；报告/文件成果交互仍未验证 |
| Research Evidence 查看/完整策略编辑 | 无 Evidence UI/专用来源策略或定时编辑器 | Run `/research/evidence`、Work requirement/revisions | Research domain/Work requirement | API 不等于完整交互，旧 Task 已退役 | 代码 | 无独立 Web 入口；聊天配置能力未验证 |
| 文档生成/编辑 | 聊天工具意图，消息文件下载/保存 | 通用发送/文件/发布；内部 create_docx 等 | File tools/OutputPublisher | 真实 DOCX 字节和最终下载成果 | 代码 | 未验证（阻断：模型）；没有专用文档编辑入口 |
| PDF/Office 转换、Heavy normalization | 聊天工具；没有专用规范化按钮 | File/Run；内部 normalize_document Activity | Document Worker/DocumentWorkflow/Gotenberg | 镜像源码/hash对齐；最终 operation/输出待验 | E00/R02/R06 | F09 部署漂移已修复；成果未验证（transform=false、模型网络阻断） |
| 长期记忆保留/跨对话召回 | 通过对话间接使用；无记忆管理页 | 无 Web memory CRUD；内部 Hindsight | ContextAssembly/MemoryRetentionService/Hindsight | completed→retain→新对话 recall | E03 | 未验证（阻断：无 completed 聊天；recall 3s 超时降级） |
| 记忆管理、reflection/metrics | 无管理入口；后台定时服务 | 内部 Hindsight/Temporal schedule | Memory service/operations | 不添加新产品要求 | 代码 | 管理无 Web 入口；后台维护不属于 Web 范围 |
| 执行 Trace 树/元数据 | Trace 按钮、右侧树/详情 | `/runs/{id}/trace` | Tracing repositories/Queries | 失败真实 Run 的五节点及 model_unavailable | E03 | 通过（树/metadata）；所有工具节点未验证 |
| 模型输入可见权限 | TraceDetail 有相关入口；当前账号 none | Run model-inputs、snapshot detail | ModelObservabilityQueries/Entitlement | none 最小列表；本人详情 403、他人 404 | E03/E08 | API 权限通过；summary/full_safe 的 UI 未验证 |
| QQ 挑战/说明/过期 | 左下绑定 QQ、注册提示 | QQ challenge POST/GET | QQ binding service | 创建挑战、说明、自动查询、过期文案 | E09 | 通过（Web 侧挑战）；绑定完成未验证 |
| QQ 身份合并/共享记忆 | Web 显示状态，确认需测试 QQ | QQ binding protocol + identity merge | Account/QQ binding/Memory | 无获准测试 QQ，不发送消息 | E09/代码 | 未验证（阻断：缺测试 QQ 身份） |
| QQ 聊天、群交互、渠道投递 | QQ 自己的入口 | NapCat/official QQ、内部 ingress/delivery | Conversation/QQ adapters/Delivery | 本轮未向真实 QQ 用户发消息 | 代码 | 不属于 Web 范围 |
| Shell/MCP/Git、内部 GC/协调器 | 部分可经聊天使用；无全面管理页 | Tool/internal runtime；非独立 Web API | Sandbox/MCP/WorkSchedule/GC | shell=false，启动 MCP 0/6；无具体功能任务 | E00/代码 | 用户工具成果未验证；内部管理不属于 Web 范围 |
| File-action 审批/拒绝 | 当前未见审批卡组件或调用 wrapper | Run approvals、approve/reject | FileActionApprovalService/Temporal wait | 当前功能受开关/入口限制 | 代码 | 无 Web 入口；不是恢复旧表单的理由 |
| 账号隔离/CSRF | 独立浏览器上下文、正常认证交互 | 多 owner GET、resource POST、mutation | Auth/所有权查询/ResourcePolicy | B 访问 A 九类 GET 404，跨授权 404，无 CSRF 403 | E08 | 通过（本轮已列资源）；Artifact/记忆隔离未验证 |
| 窄屏布局、信息清楚性 | 窄屏侧栏/聊天堆叠，控件换行；Trace 全屏可关闭 | 无独立 API | 前端布局/文案 | 390/768/1440 无横向溢出；390 输入236px | E02/R07 | F05 修复后已测页面通过；真实 Artifact 预览布局未验证 |

前端功能覆盖广，但深度不均：Workspace 的所有者日常操作较完整，真实生成成果链路、Work/Research 配置与最终输出、完整授权种类、管理/观察入口仍有覆盖缺口。不能将这张矩阵的“通过”行相加后称为全产品 E2E 通过。
