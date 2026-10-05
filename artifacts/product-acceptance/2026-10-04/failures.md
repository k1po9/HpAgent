# 失败复现、证据、分类和最小建议

以下保留初次审计的复现记录。当前修复状态及证据见 [repair-results.md](repair-results.md)，不要将下文历史“未修改”当作修复后状态。

这里只登记已观察到的失败/部署偏差。模型结果、Research 等未执行成果另列为阻断，不编造失败运行。脚本曾因等待不存在的文案超时，及因浏览器重载前仍用旧组件代码导致复测失败；这些是验收工具操作问题，不列成产品缺陷。最终上传通过是在新 Chromium 页面加载当前源码后取得的。

## F01：指定 localhost 与部署允许 Origin 不一致

- 分类：配置。高优先级，阻断 localhost 写操作。
- 复现：访问 `http://127.0.0.1:5173` → 专用账号注册/登录 → 点新建对话。
- 实际：注册 201，`/me` 200，创建 `/conversations` 403；客户端刷新 CSRF 后仍 403。后端 `public_origin=http://172.24.198.41:5173`，`csrf_guard` 对 Origin 进行精确比较。
- 证据：`runtime-baseline.json`、`network.jsonl`、`03-localhost-origin-failure.png`；请求 `req_01a1025b-6c3c-71ed-ac21-930a110cdb6a`。
- 最小建议：统一实际访问地址和 `WEB_PUBLIC_ORIGIN`。当前不改配置即可用已允许 IP 继续验收；若决定以 localhost 为标准，再只调整 API 配置并受控更新该服务。不扩大 CSRF 允许范围，不切全局代理、不重启 Docker daemon。
- 本轮处置：未改配置。localhost 仍失败，IP 路径通过。

## F02：无选中对话时，新建失败没有用户可见提示

- 分类：工程/UI。
- 复现：按 F01 点击新建；查看主区域和侧栏。
- 实际：网络已 403，但页面仍只有“选择或新建一个对话开始”。Workbench store 保存 error；没有 activeConversationId 时 App 渲染 EmptySelection，ChatPane 中的错误视图不会出现。
- 证据：`03-localhost-origin-failure.png/.txt`；`web/src/App.tsx` 与 `store/workbench.ts`。
- 最小建议：在 Workbench 的对话选择/创建公共区域显示现有 error，使未选中对话时也能解释失败；沿用现有错误类型和重试动作。无需增加新功能。
- 本轮处置：未修改，仍失败。

## F03：HTTP IP 地址下 Workspace 上传在请求前失败（已修复）

- 分类：工程/浏览器兼容。
- 复现：在已配置的 `http://172.24.198.41:5173` 登录 → Workspace 资料 → 选择小 txt 文件。
- 实际：仅显示“Workspace 操作失败：名称冲突、目录非空或目标不可用”；没有 `/workspace/uploads` 请求。该地址 `isSecureContext=false`、`typeof crypto.randomUUID='undefined'`。组件上传、保存和版本 operation key 直接调用这个函数。
- 证据：`browser-origin-context.json`、`07-first-real-run.png`（含上传错误）、初始 network 中无 upload POST；源码原调用已在本轮 diff 保留。
- 最小修复：WorkspacePanel 三处改为已有 `newIdempotencyKey()`，使用已有 getRandomValues fallback。没有自建 UUID 算法或修改 API。
- 复测：新 Chromium 载入后 upload create 201 → bytes PUT 200 → save 201；实际浏览器下载 43 bytes，hash 匹配。见 `10b-workspace-state.png`、`file-content.json` 和 E05。
- 回归：25 个相关前端测试通过；TypeScript、该文件 ESLint/Prettier、diff whitespace 检查通过。版本提交 operation key 的代码已同样修复，但 v2 成果链路未验证。

## F04：真实聊天模型决策超时，无最终助手成果

- 分类：环境/外部模型访问；具体网络或 Provider 原因尚未证明。高优先级，阻断依赖模型的成果验收。
- 复现：专用 A 账号，ReAct，发送一次短输入“请记住验收暗号青竹739，仅回复已记住，不调用外部工具”。
- 实际：发送 202，Run 从 queued 到 running，最终 failed；`failure.code=model_unavailable`。记忆查询改写成功，三次聊天 decision dispatch 为 uncertain，日志每次约 30s 模型请求失败，Temporal retry maximum_attempts=3。Embedding 也超时并使用零向量降级；MCP 启动 0/6 是独立依赖限制。
- Run：`01a1025c-ec3a-72c1-ac87-9eccc16b558a`。23:23:26 接受，23:26:03 终态（Asia/Shanghai）。
- 证据：`first-run.json`、`first-model-inputs.json`、`backend-first-run.jsonl`、`backend-db-final.json`、`08-trace-failed.png`。端点 `chat:0:minimax:MiniMax-M3`；Fake=false。
- 最小建议：先在相同 Worker 网络/同一 Provider 路径核对 timeout、端点和连通性，区分小型改写请求与含工具 schema 的聊天请求；不要先增加重试或修改全局代理。Provider 配置/网络恢复后仅复测一个小输入，再开展成果验收。
- 页面问题：只显示“运行失败”，未展示已有的安全 `failure.message=模型暂时不可用`。可以在 RunStatus 使用现有 failure message/error code，避免用户只能进 Trace 查原因。
- 本轮处置：没有额外重试/切代理/改模型配置。Work/Research/文档/Artifact/记忆成果保持未验证。

## F05：390px 窄屏布局挤压并横向溢出

- 分类：工程/UI 布局。
- 复现：已登录对话页，浏览器响应式模式 390×844，展开持续工作。
- 实际：sidebar 固定 264px，输入框只约 94px；`document.documentElement.scrollWidth=481 > innerWidth=390`。执行模式、Trace、Workspace 控件被挤到很窄的区域，文字竖向折行。
- 证据：`05-mobile-ui.png`、本轮记录的 viewport/scrollWidth；`styles.css` 固定 min-width。桌面 1440px 可操作。
- 最小建议：在既有布局中加窄屏断点，允许侧栏折叠/减小占位、工具区域和控件换行，约束 panel 宽度。本轮不扩建移动端功能。
- 本轮处置：未修改，失败。

## F06：Work SSE 的响应协商返回 406

- 分类：工程/API protocol。高优先级。
- 复现：账号有新测试 active Work → 进入工作台 → Network 查看 `/api/v1/works/{id}/events/stream?after=0`。
- 实际：前端 openSseStream 正确发送 `Accept: text/event-stream`，后端每次返回 406 `not_acceptable`。workFeed 自动恢复/重连，列表 10s 轮询仍会变化，不能因此称 SSE 成功。
- 根因源码：ProtocolMiddleware 只用 `path.endswith('/events')` 识别 SSE，把 `/events/stream` 当 JSON；同时误将 Work 普通 JSON `/events` 当 SSE。
- 证据：`http-errors.jsonl`，例 `req_01a10293-ffd3-74e1-963d-a523cf3aef29`；`web_api/app.py` ProtocolMiddleware 与 Work stream route，`web/src/sse/workFeed.ts`。
- 最小建议：按现有真实路由形状分别识别 Run `/events` 和 Work `/events/stream`；Work `/events` 保持 JSON。加入响应类型协商回归，确认合法 Accept 成功、错误 Accept 仍拒绝。没有必要改 SSE 架构。
- 本轮处置：未修改/重启 API。当前部署仍失败；UI 控制操作通过是另一条链。

## F07：取消终态后，资源面板显示授权加载失败

- 分类：工程/UI 生命周期契约衔接。
- 复现：发送新测试附件 → 立即点停止 → 观察 Workspace 资源区域。
- 实际：cancel API 已 cancelled，但面板请求该 Run `/resources` 得到 404，显示“资源授权加载失败”。ResourcePolicy.candidates 有意只提供 queued/running 的 ready snapshot；终态不是可供继续选择的资源视图。
- 证据：`13-cancel-attachment.png/.txt`、`http-errors.jsonl`、`cancel-resources.json`（重复确认仍 404），`src/workspace/resources.py:candidates`。
- 最小建议：WorkspacePanel 只对活跃、可选择资源的 Run 加载这个候选接口，终态显示适当空状态。不要把终态 404 当成授权加载故障，也不要绕开所有权检查去返回他人资源。
- 本轮处置：未修改。cancel 后端终态通过；立即 UI 终态显示未签收，不能凭一次瞬间 queued 截图断言永久卡死。

## F08：Research 空状态没有解释，配置型入口偏工程化

- 分类：工程/UI 文案与可发现性。
- 复现：新专用账号，展开“Research 输出”；在 Workspace 查看搜索/版本字段。
- 实际：Research 返回空 Work 列表时只出现空折叠内容，没有“尚无调查成果”或当前数据状态说明。许多字段用 Work/Run UUID、MIME、Destination、operation 等术语，普通用户难以理解。
- 证据：`04-configured-origin-ui.png/.txt`、`17-account-b.png`、`20-final-reloaded.txt`，ResearchOutputs 没有 works.length===0 视图。
- 最小建议：为已有列表补充明确空状态、保留现有功能的简短操作提示/字段说明。没有创建新 Research 表单或新记忆管理功能的授权；缺失入口只列覆盖缺口。
- 本轮处置：未修改，空状态失败；Research 成果仍未验证。

## F09：Document Worker 镜像执行模块落后于工作区

- 分类：部署/环境。文档验收阻断。
- 复现：只读比较 `src/document_activities/runtime.py` 与运行容器 `/app/document_activities/runtime.py` 的 SHA-256；读取差异。
- 实际：workspace `e0a6a07be76c267fd5dec3fab8a9741484deb1d4d598ff4781bd9541f17aab17`；deployed `f344f0447af5cd0f7c9087f704d7abc161369d1b7f02d1538aeefb4bedcfc804`。启动模块一致不代表 Activity 一致。
- 差异：镜像仍引用旧 `agent_execution.*` 路径、asyncio.to_thread；当前文件有 ResourcePolicy 重新鉴权、撤权检查、include_selected 以及 run_blocking 等修订，镜像缺少这些代码。没有运行攻击或真实文档，因此不声称已复现越权。
- 证据：`deployment-code-hashes.json`、`document-runtime-drift.diff`；Compose 文档服务没有源码 bind mount。
- 最小建议：后续受控部署当前 Document Worker 镜像，并核对预算/追踪相关依赖的同版本一致性；部署后先验一份小文档和撤权边界。不要在当前审计里直接重建/重启整套 Docker。
- 本轮处置：保持镜像/服务不变，文档真实执行标未验证（阻断）。transform=false 是另一个独立限制；不能简单开启后就宣布文档链路可用。

## F10：停止的未来 Work 仍展示“等待下一次到期”

- 分类：工程/UI 文案一致性。
- 复现：新未来提醒 Work → 暂停 → 恢复 → 停止；展开持续工作。
- 实际：标题状态已“已停止 · r1”，下面仍显示 continuation.at_time 及下一次到期日期。停止 snapshot、control_epoch 与调度控制已持久化；该文案不能证明后台仍在执行。
- 证据：`14-work-control.txt`、`work-stopped.json`、`20-final-reloaded.txt`。
- 最小建议：对 stopped/completed 使用既有终态文案，隐藏或明确标“停止前计划”的 continuation 到期信息；无需修改后台状态模型。
- 本轮处置：未修改。控制 API 状态通过，文案失败。

## 没有算作失败运行的阻断项

| 项目 | 阻断证据 | 分类/最小下一步 |
|---|---|---|
| Generic Work、Research、Artifact、文档创建最终成果 | chat decision 失败，缺可用助手/生成输出样本 | 环境依赖；恢复模型后按教程小样本，分别验成果和发布/保存 |
| Heavy Document/转换 | transform=false，Document image drift | 配置+部署；先对齐镜像，再核对能力开关和测试文件 |
| 长期记忆 | 新账号 recall 3s timeout 后降级，未产生 completed 聊天，无 retain outbox | 环境；先完成一段测试聊天，再验 retain 和跨对话 recall，空结果不算长期记忆通过 |
| QQ 绑定完成与账号合并 | 无获准测试 QQ，只有 Web 挑战 | 测试条件；本人使用专用测试 QQ 后再验，不自动发送给真实用户 |
| 运行中取消、动态撤权、v2/CAS | 没有真实已运行的工具/已发布新文件 | 测试条件；恢复生成能力后验证执行退出和旧新字节，不用 API 受理代替 |
| 完整通知/Work 输入/五类授权/记忆管理/对话改名 UI | 代码有对应 API 或内部能力，但没有当前交互入口 | 覆盖缺口；不补建新功能，不列为本轮通过 |
