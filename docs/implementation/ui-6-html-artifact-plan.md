# UI-6 HTML Artifact 闭环实施计划

> 状态：待实施的规划基线。制定日期：2026-10-08（Asia/Shanghai）。
> 依据《HpAgent 前端 UI 重构方案 v1.0》及 UI-5 完成后的实际代码。本文仅描述后续实施与验收安排，不代表 UI-6 已实现或测试已通过。

## 1. 基准、目标与范围

| 项目 | 本次核验基准 |
| --- | --- |
| 仓库 | `/home/hp/workspace/HpAgent_web` |
| HEAD | `9560c99675d6ebfd159909ea9d812790572c6a1d` |
| 提交 | `feat(web): implement UI-5 task center and fix review findings` |
| 提交时间 | 2026-10-08 00:39:38 +08:00 |
| 初始工作区 | `git status --short` 为空 |
| 方案 | `D:/HuaweiMoveData/Users/黄培/Desktop/ui/HpAgent_前端_UI_重构方案_v1.0.md` |
| WSL 路径 | `/mnt/d/HuaweiMoveData/Users/黄培/Desktop/ui/HpAgent_前端_UI_重构方案_v1.0.md` |
| 方案 SHA-256 | `debffa18de54203fa2e7037108b9f0b3f736e6b350516a9c96fd87590217f892` |
| 主要依据 | 方案 §7、§8、§9、§10、§12–14；D01/D05/D07/D08/D09/D10；FE-B1/FE-B3/FE-B5 |
| 前序记录 | [UI-4 实施报告](ui-4-workspace-permissions-report.md)、[UI-4 自检修复](ui-4-self-review.md)、[UI-5 实施报告](ui-5-task-center-report.md)、[UI-5 自检修复](ui-5-self-review.md) |
| 核验方式 | 静态阅读前后端源码、现有测试及文档；未运行应用测试、启动服务或写入业务数据 |

附件中的实施措辞作为产品设计输入。本次授权范围是制定计划与生成 Markdown，交付本文及文档索引；实际实施时重新记录 HEAD、dirty diff 和前序回归结果。

**交付目标：** 用户从 AI 消息或 Task 原交付引用打开同一个 HTML Artifact Inspector，查看真实版本、提交修改、交互预览、下载指定版本和显式保存源码副本；关闭后恢复原上下文，手工新版本与原任务验收保持清晰边界。

| UI-6 纳入 | UI-6 不纳入 |
| --- | --- |
| AI/Task 上下文入口、三页签 Artifact Inspector、历史版本与生成反馈 | 第四个一级入口、全局成果库、非 HTML Artifact |
| Shell 作为选择权威，迁移 Artifact UI 字段及相关调用点 | Run/Work/Artifact/Workspace 持久化模型和 SSE 协议重写 |
| 修改草稿、命令幂等、同对象提交锁、查询竞态、轮询恢复 | 任意历史版本分支、Artifact cancel API |
| 复用 UI-4 保存操作，补 Artifact 源信息、文案和结果回链 | 一等 HTML 文件保存/持久 provenance（BE-AW1） |
| Task 原交付与正在查看版本的区分，沿用原验收资格 | 手工版本替换原 Task 交付（BE-A2） |
| 本模块响应式、焦点、减少动效与回归证据 | UI-7 全站旧页壳/CSS 清理、UI-8 全链路最终发布验收 |

## 2. 代码现状与实施差距

以下链接相对仓库定位；以函数名作为检索锚点，避免沿用原方案旧 HEAD 的行号。所有差距均来自静态阅读，未声称已在运行环境复现。

| 编号 | 当前源码事实 | UI-6 决策 |
| --- | --- | --- |
| C6-01 | [App](../../web/src/App.tsx) 已为 Pre-Shell；[AppShell](../../web/src/components/shell/AppShell.tsx) 仅 AI/空间/任务，Artifact 已通过 Inspector 打开 | 不再安排“移除 App 自动跳成果页”这项已完成工作；回归确认无导航倒退 |
| C6-02 | [shell](../../web/src/store/shell.ts) 已支持 artifact/version、有限返回栈和 expanded；Artifact tab 白名单只有 preview/versions；origin 没有 revision | 增加 details 合法值及任务来源描述；统一 selection/URL/返回/焦点恢复 |
| C6-03 | [InspectorHost](../../web/src/components/shell/InspectorHost.tsx) 对 Artifact 调用旧 openArtifact(false)，子树 key 含 versionId；Host 已拦截显式不存在版本 | 保留“版本不可用”保护；换版本不卸载整个修改区，替换为专用 ArtifactInspector |
| C6-04 | [ArtifactPanel](../../web/src/components/ArtifactPanel.tsx) 只有前后版本按钮、预览和单行修改框；仍可回退读取 store.open 字段，未匹配版本时取列表最后一项 | 三页签与对象行；显式版本不可用时不得静默替代；默认选择与手动选择分开 |
| C6-05 | ArtifactPanel 调用 createVersion 后立即清空指令；无提交锁、4000 上限、最新成功 parent 提示及未结束构建检查 | 成功确认后按草稿版本清空；失败保留；明确最新成功版修改语义 |
| C6-06 | [artifacts store](../../web/src/store/artifacts.ts) 已有账户 generation、selectionGeneration、navigationToken 与按 generation/version 去重的 pollers；退避为 1/2/3/5 秒 | 复用现有保护和退避，不重建轮询；补对象查询状态、权限失效处理及多未完成版本恢复 |
| C6-07 | openArtifact 只恢复最后一项的 poll；createVersion 每次生成新 key、返回 void 并吞错；error 为全局值；构建完成只更新版本缓存 | 对每个 queued/running 版本去重恢复；返回结构化命令结果；同意图保留 key，错误按对象/操作隔离，完成后更新消息摘要 |
| C6-08 | [HpThread](../../web/src/adapters/assistant-ui/HpThread.tsx) 点击时查询已有 Artifact，默认打开最后一个，已有时提供“再生成一个”；canBuild 只检查 assistant/complete | 加非空正文前置条件、明确 HTML 文案、已有对象列表和创建单次锁；补 conversation/message origin |
| C6-09 | [TaskOutputs](../../web/src/components/tasks/TaskOutputs.tsx) 已以确切 artifact_version_id 打开原引用，并明确手工版不替代交付；[taskActions.acceptanceEligible](../../web/src/components/tasks/taskActions.ts) 已检查 role/revision/epoch/成功 Run/evidence | 保留资格与 UI-5 命令控制；补 Inspector 内原交付提示和返回任务入口，不再造一套 accept-result |
| C6-10 | [SaveToWorkspaceDialog](../../web/src/components/workspace/SaveToWorkspaceDialog.tsx) 已从 TestPages 提取；[workspaceOperations](../../web/src/components/workspace/workspaceOperations.ts) 已冻结源、保留 upload/save key、ready file_id、处理 409 改目标及迟到完成 | 不重复提取或重写保存状态机；补 Artifact/version 标识、源码命名、成功文案和 node_id 回链 |
| C6-11 | AppShell 仍经 TestPages 导入保存弹窗；onSaveHtml 为字符串回调并补 `.txt`，成功只显示通用“已保存到空间” | 直接导入正式保存组件；改为类型化源码描述；保留现有文件保存调用者兼容性 |
| C6-12 | [ArtifactPreview](../../web/src/components/ArtifactPreview.tsx) 已用 srcDoc、sandbox="allow-scripts"、event.source 检查；runtimeError 未随 html 改变清除 | 保持安全边界；按版本隔离预览错误，验证旧 iframe 消息与新版本切换 |
| C6-13 | [HpArtifactVersion](../../web/src/api/types.ts) 未声明 producing_*；[服务端 DTO](../../src/web_artifacts/services.py) 已输出 producing_run_id/execution_id/operation_id，但没有 file_id | 必要时补真实 nullable 来源字段；不能猜测 file_id；输出文件仍从 Work/Run 的真实文件引用使用 |
| C6-14 | [sessionLifecycle](../../web/src/store/sessionLifecycle.ts) 已统一 reset Artifact 和 Workspace 操作；[artifact E2E](../../web/e2e/artifact.spec.ts) 仍用旧按钮名称、固定下载名并把截图写入 ui-2 | 新草稿/命令/Blob 生命周期接入现有 reset；迁移受影响 E2E 并建立 ui-6 独立证据目录 |

UI-6 应重点补 C6-04～C6-08 和 C6-11，不能把前序已完成的 Shell、poll 去重及保存恢复重新记作本阶段新增成果。

## 3. 目标结构与状态所有权

建议文件名如下，均为待实施设计；小型纯展示组件可合并，但查询、命令和导航职责保持独立。

```text
web/src/components/artifact/
  ArtifactInspector.tsx       # 预览 / 版本历史 / 详情，来源和页面状态
  ArtifactVersionHistory.tsx  # 真实版本号、parent、指令、时间、状态
  ArtifactComposer.tsx        # 修改草稿、基准版本提示、提交反馈
  ArtifactMessageItems.tsx    # AI 已有对象行与显式创建入口
web/src/store/
  artifacts.ts               # 现有实体、对象查询、构建轮询
  artifactUi.ts              # 建议新增：账户内按 Artifact 缓存的草稿
web/src/components/artifact/
  artifactOperations.ts      # 建议新增：创建/修改命令意图及锁
```

| 状态 | 唯一权威 | 规则 |
| --- | --- | --- |
| 当前对象、版本、tab、origin、返回栈、扩大阅读 | useShell | 不复制 HTML/Work 真相；version/tab 更新使用 replace，主动打开对象使用 push |
| Artifact/Version、消息摘要、loading/error、轮询 | useArtifacts | 查询按账户世代+对象，响应按请求 token；错误不再共享一个全局槽 |
| 未提交 instruction | Artifact UI 内存缓存 | 按 account/Artifact；换版本、关面板再开保留，退出清空，不持久化到 URL/localStorage |
| 创建/修改操作 | artifactOperations 或等价 store 内命令层 | 固定 payload/key、busy/uncertain/result；异步完成回写原对象 |
| 保存过程 | 现有 useWorkspaceOperations | 不创建 Artifact 专用上传器；记录源身份仅为当前会话说明，不冒充持久 provenance |
| Task 验收 | useWorks + taskOperations + acceptanceEligible | Artifact 面板只回到任务验收上下文 |

### 3.1 迁移顺序

1. 将加载实体与导航拆开：以 loadArtifact/refreshVersions 等纯查询动作取代 Host 的 openArtifact(false)。具体命名可调整。
2. ArtifactInspector 只读取 Shell 的选中描述；无 version 的首次有效加载选定默认版本后 replace 写回地址。
3. 迁移 HpThread、TaskOutputs、ArtifactsPage 临时创建入口及旧 hash 上下文读取，搜索全部 openArtifactId/openVersionId/selectVersion/closeArtifact 调用点。
4. 删除领域 store 的 openArtifactId/openVersionId 和选择回调。确有暂存旧调用者时，只保留“发送 Shell 导航意图”的无状态单向适配器，并登记 UI-7 删除清单；不保留第二份 selection。
5. InspectorHost 不再以 versionId 重建整个 Artifact 子树；预览 iframe 可以按版本单独重建。复用唯一 Surface，避免嵌套第二个 Inspector 外壳。

## 4. 交互与行为契约

### 4.1 AI、Task 及深链入口

- AI 仅对已完成且正文非空的 assistant message 开放“生成 HTML”。生成只发生于明确点击；已有对象以标题、HTML、最近已知版本状态列出，支持逐个打开，不只打开最后一个。
- 已加载消息的 Artifact 查询应去重并限并发；可在消息可见时加载，避免整个历史列表无界请求。查询失败提供重试，不把失败当成空列表触发创建。
- 打开对象带 conversationId/messageId 或 workId；Task 来源额外保存 source requirement revision 和原交付 versionId，不能被浏览版本覆盖。origin 仅为导航/说明，不能作为权限或验收证据。
- Task 使用现有 Work.artifacts 精确引用。conversation_id=null 合法；Research Markdown 报告沿用当前报告接口，不转成另一种 Artifact。
- 深链复用 `#/ai/{id}?inspect=artifact:{id}&version={versionId}&tab=preview` 与 `#/tasks?work={workId}&inspect=artifact:{id}&version={versionId}`；details 加入解析白名单。标识必须经真实 API 重新验证。
- 刷新 Task 深链时内存 origin/返回栈可能缺失，可从 route.workId 加载真实 Work 并匹配引用；无法匹配只展示“来源待核实”，不推测原交付版本。没有来源时不枚举全账户任务反查。
- 关闭/返回恢复筛选、消息位置和聊天草稿；不重建聊天 runtime、不停止后台任务。补 Artifact 触发按钮稳定焦点标识；原按钮消失时回 Canvas 标题。

### 4.2 Inspector 内容

| 区域 | 内容与规则 |
| --- | --- |
| Header | 真实标题、HTML、正在查看 vN、所选版状态、关闭/返回/扩大阅读；长标题可换行 |
| 预览 | completed 且有 html 时 iframe；构建或失败时可由用户打开最近成功版，清楚标明预览版本 |
| 版本历史 | 依据 version 排序；展示真实 parent_version_id 对应版本、instruction 和已有时间，不用数组位置假造版本号 |
| 详情 | 创建/完成时间、来源对话或任务、可核实的 producing Run；内部 ID/失败 code 放详情，不占主阅读区 |
| Actions | “下载 HTML”“保存源码副本到空间”；仅对选中的 completed+html 开放 |
| Artifact Composer | 独立修改指令；不发送聊天消息；显示“基于最近成功版本 vN 修改”及切换入口 |

### 4.3 版本选择与异常状态

| 情况 | 必须行为 |
| --- | --- |
| 无显式版本 | 按真实 version 数值取最近 completed；没有成功版则取最新构建版；无版本显示 empty |
| 明确 versionId | 只展示该版本；不存在/不属于当前 Artifact 时 unavailable，提供显式切换入口，不静默回退 |
| 新版本构建中 | 保留正在查看版本，显示构建 vN 状态；用户可主动查看构建版或上一成功版 |
| 新版本完成 | 更新缓存和版本历史，提供“查看 vN”，不抢走用户选中的历史版 |
| 构建失败 | 展示真实 failure；旧成功版仍可读；“再次生成”是新意图，不伪装原版本恢复成功 |
| 网络失败/5xx | 保留已知内容，提示同步中断并重试 GET，不修改 build status |
| 403/404 | 清除相应对象/版本可见内容和操作，终止对应无效轮询；不将权限失败无限作为网络重试 |
| 401/账户改变 | 接入统一 session reset，使请求、草稿、操作结果和预览全部失效 |
| A→B→A、关闭后迟到 | 旧响应不得覆盖新选择或错误；已接受命令可更新同账户的原对象缓存，不能自动弹回旧对象 |

保留现有每 version 一个 poller 与 1/2/3/5 秒退避。重开时枚举所有 queued/running 版本并复用 poller，不能仅检查 latest。关闭 Inspector 不发取消命令；当前账户的已知构建监控由 store 持有，退出停止。新版本终态同步其消息摘要；无需全应用重载。

### 4.4 修改与幂等

1. 指令 trim 后非空、最多 4000 字符；计数与服务端长度规则对齐，覆盖 emoji/中文。当前输入法 composition 中 Enter 不提交，采用明确按钮或安全快捷键。
2. 按 Artifact 加本地提交锁。提交前刷新版本列表；发现 queued/running 则禁止再发，展示正在构建的版本。
3. 记录展示给用户的最新成功 parent。若预检发现 parent 已改变，保留指令并要求用户审阅新基准后再提交；无成功版时说明会重新生成、尚无成功版可继承。
4. 只发送 `{instruction}`，不发送 parent_version_id。跨标签在预检之后仍可并发，服务端返回的 parent 是事实；不能承诺乐观预检提供服务端并发锁。
5. 一次意图固定目标、instruction、Idempotency-Key。busy 时连点不产生第二条命令；网络/5xx 响应不确定时保留 key，只允许恢复该意图，不把“重试”当作新修改。
6. 返回结构化结果，例如 success/failed/uncertain/stale，附实际版本或错误。只有确认成功且草稿仍等于提交时的草稿 revision 才清空；等待期间新输入不能被旧响应清除。
7. 确定拒绝后保留草稿，按错误反馈修正；明确再次生成或更改参数才新建 key。首次从 Message 创建 Artifact 也执行同样的意图恢复，不能仅修 createVersion。
8. 命令成功只更新原 Artifact 缓存；当前页面已离开时不打开面板。若版本选择已变化，不替用户切版。错误保留 code/request_id（可用时）于详情。

接口未提供客户端任意 parent 或预期 parent 版本控制，这一限制由 BE-A1 承接；本阶段不加后端协议。

## 5. 预览、下载与保存

### 5.1 预览和下载

- 保持 `sandbox="allow-scripts"`，不增加 allow-same-origin；HTML 只进入 iframe srcDoc，不注入宿主 DOM。
- 保持 event.source === 当前 iframe.contentWindow 检查，仅处理预览错误提示。iframe 消息不触发保存、授权或其它服务器命令。
- 切 Artifact/版本重置 runtimeError；验证上一 iframe 迟到消息不影响新预览。“重新加载预览”只重建 iframe，不触发构建。
- 下载冻结点击时选中的 completed version，生成 text/html Blob，使用清理非法文件名字符后的 `标题-vN.html`；及时释放 Object URL，退出和卸载也要清理尚未释放的 URL。

### 5.2 两条保存链

| 来源 | 操作链 | 成功含义 |
| --- | --- | --- |
| 既有 output file_id | 选目录/名称 → 现有 saveWorkspaceFile | 保存该文件的空间入口，不重新下载上传 |
| Artifact 源码 | 冻结 artifactId/versionId/version/title/html → text/plain 上传 → PUT → saveWorkspaceFile | 保存 HTML 源码副本，默认 `标题-vN.html.txt`；交互预览仍在 Artifact |

前端 ArtifactVersion DTO 与服务端版本 DTO 当前都不能提供用于保存的 file_id；不能从 versionId、标题或 Run 列表顺序猜出映射。既有 file_id 路径继续从 Task/Run 发布文件及消息文件入口使用。

在现有 SaveSource HTML 分支补充可选的 Artifact 源身份，或新增兼容的 discriminated union。保持所有已有 file_id 调用者可用；这些字段只进入客户端操作记录，不添加不存在的后端请求字段。

**复用并补验现有状态机：**

```text
选择目录/名称 → 初始化上传 → 上传内容 → 保存空间入口 → 成功
                    │             │             │
                    └─同 key恢复──┴─核实 ready──┴─复用 file_id/saveKey
```

- 源码分支不传授权 subject；“保存”不自动授权，不自动接受 Task，不自动建立版本关联。
- 上传成功后保存失败只恢复保存；PUT 响应丢失先读 metadata 判断 ready，不盲目重传内容。
- 保存响应不确定时固定原目标和 key；明确同名 409 后才允许改名/改目录作为新保存意图，复用 ready file_id，不静默覆盖。
- 用户在等待时切版本或关闭弹窗，原操作源不改变；旧完成不得关闭新弹窗、显示新版本保存成功。重开可恢复未完成操作，退出清空客户端记录并隔离迟到响应。
- 保存名和确认区域明确 `.html.txt` 为源码文本副本；如果允许自定义名称，仍不可暗示 MIME 已变成 HTML。
- 成功反馈区分“源码副本已保存到空间”与普通文件保存，包含原版本/文件名；从实际操作返回 node_id 提供“打开空间”。改目标恢复后也应保留源码来源说明。
- 构建新版本不更新已保存副本；再次保存必须明确触发。刷新后不伪造 Artifact↔Workspace 关联。

BE-AW1（一等版本保存、MIME 与持久 provenance）独立排期，不阻断本阶段源码副本保存交付。

## 6. Task 验收与真实 API 边界

### 6.1 原交付版本固定

从任务 rN 的交付 v1 进入，生成 v2 后，Task 仍显示原引用 v1；Artifact Header 显示正在查看 v2，同时提示“此版本由后续修改生成，不自动替代任务交付”。原版本描述来自 Work.artifacts 与实际版本数据，不能用当前选择回填。

Artifact Inspector 不增加通用“接受最新版”按钮。需要验收时返回原 Task 的成果页，继续使用 UI-5 的 acceptanceEligible、成功 Run 证据及 taskOperations；需要改变目标则进入原有“修改要求”流程。手工新版本对应新的 artifact_build Work，不改原 Task role/revision/evidence。

UI-6 回归必须覆盖：role=deliverable、当前 source_requirement_revision、completed、尚未被当前 revision 接受、receipt_ref 定位，以及成功 Run 的 work_id/revision/control_epoch/evidence。前端只能缩小候选，最终服从 accept-result 事务结果；409/拒绝保留上下文并刷新，不自动重复接受。

### 6.2 现有接口

以下路径均含真实公共前缀；以 [HpApi](../../web/src/api/resources.ts)、[ArtifactService](../../src/web_artifacts/services.py) 和 [Work integration](../../src/work_domain/integration.py) 为准。

| 能力 | 现有接口/方法 | 接线约束 |
| --- | --- | --- |
| 消息对象列表 | GET `/api/v1/messages/{id}/artifacts` | 有界、去重、失败不当作空 |
| 从消息创建 | POST `/api/v1/messages/{id}/artifacts` | source 必须合法；复用幂等 key |
| 对象/版本列表 | GET `/api/v1/artifacts/{id}`、GET `/api/v1/artifacts/{id}/versions` | 返回服务端 version/status/parent |
| 创建新版本 | POST `/api/v1/artifacts/{id}/versions` | instruction；最新 completed parent 由服务端决定 |
| 单版本状态 | GET `/api/v1/artifact-versions/{id}` | 沿用查询轮询，无新 SSE、无 cancel |
| 文件保存 | createWorkspaceUpload/uploadContent/getFile/saveWorkspaceFile | 复用现有 wrapper/协议和 UI-4 恢复 |
| 任务接受 | POST `/api/v1/works/{id}/accept-result` | 复用 Work 控制、If-Match 与证据校验 |

仅补服务端确已返回的 DTO 字段；数据库有字段不等于公开 DTO 已返回。BE-A1、BE-A2、BE-AW1 均不得伪装成现有前端能力。

## 7. 实施工作包与顺序

依赖：UI-1～UI-5 的现有实现和定向回归可用 → U6-0 → U6-1 → U6-2 → U6-3 → U6-4 → U6-5 → U6-6。这是实施顺序，不是本次执行授权。

| 工作包 | 主要改动 | 退出标准 |
| --- | --- | --- |
| U6-0 基线与契约 | 复核本表源码、现有测试、所有 Artifact 调用点和 UI-5 验收保护 | 记录 HEAD/dirty diff；明确已有失败与新增反例；确认无新增后端契约 |
| U6-1 状态分离 | artifacts 查询/错误分桶，Shell 选择权威，草稿与命令意图、session reset | 领域 store 无第二份 selection；A→B→A/退出竞态通过；旧入口可达 |
| U6-2 Inspector 与入口 | ArtifactInspector 三页签、消息对象行、Task origin、版本路由/焦点 | AI/Task/刷新可打开；历史版不被切走；不可用无静默回退；无成果页 |
| U6-3 修改与构建 | ArtifactComposer、预检与锁、返回结果、key 恢复、全部未完成版本轮询、摘要同步 | 草稿不丢、连点不重复、网络错误不伪造失败、实际 parent 可核查 |
| U6-4 下载保存 | Preview 错误隔离、Blob 清理、类型化保存源、UI-4 恢复复用、结果回链 | 下载版本准确；源码 `.html.txt`；ready 后不重传；切版不污染保存 |
| U6-5 Task 边界与布局 | 原交付提示、验收回链；单 modal、移动双 Composer、键盘/IME/reduced-motion | v2 不成为原 Task 交付；无背景误操作；焦点逐层恢复 |
| U6-6 验收交接 | 定向及全套前端检查、受影响 E2E、后端契约场景、截图/记录 | 全部退出项有证据；未覆盖项明确；记录 UI-7 待清理 bridge/旧壳 |

### 7.1 文件影响清单

| 文件/目录 | 修改目的 |
| --- | --- |
| `web/src/store/artifacts.ts` | 保留实体和轮询，拆导航；补命令结果、查询隔离、终态摘要刷新 |
| `web/src/store/shell.ts`、`shell.test.ts` | details tab、来源/焦点描述、版本 replace、旧 hash 兼容 |
| `web/src/store/sessionLifecycle.ts` | 新草稿/命令/下载清理接线 |
| `web/src/components/artifact/*`（建议新增） | Inspector、版本、修改、消息对象展示及对应定向测试 |
| `web/src/components/ArtifactPanel.tsx`、`ArtifactPreview.tsx` | 旧面板迁移/临时适配；安全预览保留与错误隔离 |
| `web/src/components/shell/InspectorHost.tsx`、`AppShell.tsx` | 正式面板与保存源回调接线，去除 TestPages 保存重导出依赖 |
| `web/src/adapters/assistant-ui/HpThread.tsx` | 非空消息创建、对象列表、origin、状态反馈 |
| `web/src/components/tasks/TaskOutputs.tsx` | 原交付来源描述与回链；保留已有资格和命令路径 |
| `web/src/components/workspace/SaveToWorkspaceDialog.tsx`、`workspaceOperations.ts` | 最小增补来源和结果反馈，不另写上传协议 |
| `web/src/api/types.ts` | 按需补 producing_* nullable 字段；不添加虚构 file_id |
| `web/src/components/TestPages.tsx` | 仅迁移受影响 Artifact 调用点，保留尚有调用者的能力；全面清理留 UI-7 |
| `web/src/styles.css` | 仅新增/替换 Artifact 局部布局和响应式样式 |
| `web/e2e/artifact.spec.ts`、建议新增 `ui-6-artifact.spec.ts` | 迁移原断言并覆盖版本/修改/恢复/Task 隔离 |

## 8. 验收矩阵

| ID | 场景 | 通过条件 | 优先验证层 |
| --- | --- | --- | --- |
| A6-01 | AI 创建、多个已有对象、空正文 | 创建有显式意图；全部对象可打开；空正文不发 POST | 组件 + E2E |
| A6-02 | Task 无 Conversation 的 Artifact | 精确打开原版本；返回保留任务筛选及成果页 | 组件 + E2E |
| A6-03 | 无版本/显式不存在版本/详情深链 | 默认最近成功；缺失版 unavailable；刷新不丢合法 tab | store + Shell + E2E |
| A6-04 | 正在看 v1，v2 完成 | 不自动切版；历史/摘要更新；用户可主动查看 v2 | store + 组件 |
| A6-05 | 历史版修改与跨标签并发 | 文案指向最近成功版；请求无 parent；显示实际返回 parent | 组件 + 后端契约 |
| A6-06 | 修改失败、等待时编辑、4000 边界 | 草稿保留；旧成功不清新草稿；中文/emoji/IME 正确 | 组件 |
| A6-07 | 连点创建/修改与响应丢失 | 单意图单 key；恢复重放不产生重复 Artifact/版本 | store + 真实 API |
| A6-08 | 多个未完成版本、StrictMode、关开 | 每 version 单 poller；全部能恢复；无 cancel/重复聊天 feed | store + 集成 |
| A6-09 | 网络恢复、构建 failed、403/404 | 三者分别展示；网络不写 failed；权限失效清正文并停止无效轮询 | store + 组件 |
| A6-10 | A→B→A/关闭/切账户后的 GET/POST/poll | 迟到不抢选择、不污染错误；账户更换清 HTML/草稿/key/定时器 | 延迟 Promise 测试 |
| A6-11 | iframe sandbox/伪造消息/切版本错误 | 无 same-origin；外部 source 无效；错误只属于当前预览 | 组件 + 浏览器 |
| A6-12 | 下载历史版和退出 | 字节与所选 HTML 相同；正确文件名/MIME；URL 释放 | 组件 + 浏览器下载 |
| A6-13 | 源码保存成功 | text/plain、默认 `.html.txt`；无 grant/accept 调用；真实 node 回链 | 组件 + E2E |
| A6-14 | 上传 ready 后保存失败/响应丢失 | 重试复用 file_id/key；不重复 PUT；未确认结果不改目标 | 操作层 + 真实 API |
| A6-15 | 同名 409 改名、保存时切版/换源 | 复用 ready 内容；不覆盖；旧完成不关闭新弹窗 | UI-4 回归 + E2E |
| A6-16 | 既有 output file_id 保存 | 不创建上传、不下载重传，保留原工作流 | 组件 + E2E |
| A6-17 | 原任务 v1 → 手工 v2 → 回到 Task | 原交付仍 v1；v2 无接受通路；未发 accept-result/reference 冒充交付 | UI-5 回归 + 后端契约 |
| A6-18 | revision/epoch/evidence 过期、验收 409 | 保守禁用或服务端拒绝后刷新，不能接受错误版本 | taskActions + 真实后端 |
| A6-19 | 键盘、modal、Esc、长标题、200% 文本 | 焦点逐层进入/返回；背景 inert；按钮可达，无溢出遮挡 | 浏览器 + 人工 |
| A6-20 | 手机双 Composer、软键盘、减少动效 | 只操作前景 Artifact Composer；输入不被遮挡；关闭动效功能一致 | 多尺寸截图 + 人工 |

A6-17/A6-18 对应 FE-B5；A6-02/A6-03/A6-08 对应 FE-B3；A6-10 对应 FE-B1。UI-5 M01–M21 保持回归，不因 Artifact 接线放宽任务分类或验收。

### 8.1 已有测试基础与待补内容

- `web/src/store/artifacts.test.ts` 已包含 A→B→A、迟到版本创建、poller 去重、reset、网络恢复等测试定义；迁移断言时保留其语义，补命令 key/多构建恢复/权限失效。
- `web/src/components/ArtifactPreview.test.tsx` 已检查 sandbox；补消息来源、版本切换错误清除和旧 frame 迟到消息。
- `web/src/components/workspace/WorkspaceCommands.test.tsx`、`UI4SelfReview.test.tsx` 已有保存换源、迟到完成和冲突恢复基础；扩充源码版本冻结，不删除原断言。
- `web/src/components/tasks/taskPresentation.test.ts`、`TaskSelfReview.test.tsx` 保留验收候选和自检修复回归；补原交付 v1/手工 v2 的集成场景。
- `web/e2e/artifact.spec.ts` 保留生成、交互、sandbox、下载、保存、刷新恢复，更新预期名称和截图目录；新增失败恢复与 Task 跨面板场景。

以上仅说明已检查测试源码存在，本次没有运行这些测试。

### 8.2 后续执行命令与环境

先运行改动相关测试，再执行阶段全套检查；以下为实施后的命令，不是本次运行结果。

```bash
cd /home/hp/workspace/HpAgent_web/web
npm test -- src/store/artifacts.test.ts src/store/shell.test.ts src/components/ArtifactPreview.test.tsx
# 同时执行新建 Artifact 用例及受影响 Workspace/Task/生命周期测试。
npm run typecheck
npm run lint
npm run build
npm test
# 先按 docs/development/testing.md 配置隔离基础设施。
npm run test:e2e -- e2e/artifact.spec.ts e2e/ui-6-artifact.spec.ts e2e/ui-5-tasks.spec.ts e2e/ui-5-recovery.spec.ts e2e/ui-4-workspace.spec.ts
```

`ui-6-artifact.spec.ts` 为建议新增文件，建立后再运行该命令。受影响 auth/multi-tab/SSE 用例在迁移实际触碰生命周期时纳入最终回归。

```bash
cd /home/hp/workspace/HpAgent_web
# 仅在独立、可丢弃测试库及正确角色 DSN 已配置后执行持久化/API 用例。
PYTHONPATH=src python3 -m pytest test/test_web_artifacts.py test/web_persistence/test_web_artifacts.py test/web_persistence/test_work_integration.py test/web_api/test_workspace_v41_p1.py -q
```

参照 [测试指南](../development/testing.md)：PG/API fixture 可能清表；使用独立测试库、Redis DB、文件目录和端口。Playwright 保持 workers=1，不能并发同账号用例。若已有测试不覆盖 parent/验收拒绝/同 key 重放的关键前提，补对应定向契约场景，不能只引用文件名视为通过。

至少检查 360×800、390×844、768×1024、1024×768、1280×800、1440×900、1920×1080；截图覆盖打开/关闭、loading/empty/error/unavailable、失败构建、长标题、保存恢复和 reduced-motion。截图不能代替真实软键盘与键盘焦点验收。

## 9. 证据、退出标准与交接

建议交付：

```text
docs/implementation/ui-6-html-artifact-report.md
artifacts/product-acceptance/ui-6/
  README.md                  # 基线、范围、A6 映射、限制
  logs/                      # 命令、退出码、失败及修复后结果
  screenshots/               # 尺寸/状态/版本明确的截图
  flows/                     # 成功、失败恢复、并发/迟到流程
```

实施完成需同时满足：

1. AI/Task 与深链均进入唯一 Inspector，原页面、聊天草稿和任务筛选可恢复；只有三个一级入口。
2. Shell 是版本选择权威，历史版不被新结果抢占；没有静默替换不存在版本。
3. 创建/修改失败不丢草稿，响应不确定恢复保持原 key；构建轮询无重复、可恢复且账户隔离。
4. sandbox/source 检查不放宽，下载所选版本，Blob 清理可验证。
5. 源码保存准确命名、说明 text/plain 副本，ready 内容不重传；已有 file_id 路径不回归；保存不自动授权/验收。
6. UI-5 原交付与 acceptanceEligible 保留，v2 不自动成为原 Task 验收成果；后端证据拒绝路径有实测。
7. A6-01～A6-20 分别记录通过/失败/未覆盖；前端检查与受影响 E2E 通过，无未解决 P0/P1。未满足必验项不得标记完整验收通过。
8. 报告分开写静态检查、模拟执行器 E2E、真实后端状态链和真实模型/Temporal/渠道覆盖。Fake Executor 不能证明真实生成质量或生产持久执行已通过。

UI-7 接收尚有调用者的旧 Artifact 页面壳/单向适配器和局部旧样式清单；UI-8 接收跨模块性能与完整产品回归。BE-A1/BE-A2/BE-AW1 保持独立标签，不阻断本阶段已冻结范围，也不能被标记为已交付。

回退以对应前端提交/视图接线为单位，保证旧入口仍有能力映射；不删除服务器 Artifact/Work/文件、不反改 Task 状态、不回滚用户保存内容。本阶段不需要数据库迁移。
