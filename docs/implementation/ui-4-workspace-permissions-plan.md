# UI-4 空间与权限实施计划

> 状态：待实施。制定日期：2026-10-07（Asia/Shanghai）。
> 依据：《HpAgent 前端 UI 重构方案 v1.0》与当前项目源码。本文只交付实施设计，不代表功能已实现或验收已通过。

## 1. 基准、目标与阶段边界

### 1.1 核验基准

| 项目 | 本次基准 |
| --- | --- |
| 项目 | `/home/hp/workspace/HpAgent_web` |
| HEAD | `40a318a5600a3123f3149e2788ddafaf28d59e9c` |
| 提交 | `fix(web): refresh run outputs and invalidate revoked model inputs` |
| 提交时间 | 2026-10-07 13:15:37 +08:00 |
| 开始时工作区 | `git status --short` 为空 |
| 重构方案 | `D:/HuaweiMoveData/Users/黄培/Desktop/ui/HpAgent_前端_UI_重构方案_v1.0.md`；WSL 对应 `/mnt/d/HuaweiMoveData/Users/黄培/Desktop/ui/HpAgent_前端_UI_重构方案_v1.0.md` |
| 主要依据章节 | §3、§5、§8–11、§12–14；冻结决策 D01/D03/D07/D08/D09/D10 |
| 前序阶段 | 代码已接入 UI-1/UI-2/UI-3；[UI-3 报告](ui-3-execution-diagnostics-report.md)及[自检修复记录](ui-3-self-review.md)作为历史证据，实施前复核当时最新 HEAD |
| 本次验证性质 | 静态阅读前后端代码、测试和文档；未运行业务服务、前端测试、浏览器或数据库测试 |

附件内的执行措辞作为设计输入。本次用户请求是制定 UI-4 实施计划和生成 Markdown，因此本次仅新增计划及文档导航，不实施 UI、不安装依赖、不提交或发布。

**交付目标：** 用户能通过空间目录树和文件表格定位资料，在唯一 File Inspector 中预览、下载、查看来源与版本，显式将资料授权给 Conversation 或 Work；上传、保存、授权、移除入口、内容版本提交分别表达真实结果，失败后能够从已完成步骤继续。

### 1.2 范围与依赖

| UI-4 必须交付 | 保留或后续交付 |
| --- | --- |
| Workspace Sidebar、Tree、FileList、面包屑、全空间搜索及分页 | UI-5 任务四桶、任务主体选择器之外的完整任务分页与任务中心重构 |
| File Inspector 四页签；目录信息及操作复用同一 Host | UI-6 Artifact 版本、修改、执行预览和成果验收边界 |
| 空间独立上传、默认仅保存、可选显式授权、分阶段恢复 | 不改临时聊天附件的 file_ids 提交链 |
| 共享 ResourcePicker/GrantEditor，分别接 Conversation/Work 契约 | 不重写 Work/Run/Workspace/Artifact 领域、数据库或 SSE |
| impact 确认、改名/移动/移除、版本升级与 CAS 冲突另存 | 不做批量危险操作、回收站、拖拽自动授权、Office 原地编辑 |
| 保存对话输出与 HTML 文本副本的公共保存基础、缓存失效 | UI-6 继续负责 Artifact 保存入口和完整闭环，UI-7 清理剩余旧页壳 |
| 本阶段相关响应式、键盘、焦点、错误和竞态回归 | UI-7 全站动效/样式统一；UI-8 全链路综合交接 |

UI-4 依赖已有 Shell、InspectorHost、会话生命周期与 UI-3 Run 上下文，不依赖 BE-W1～BE-W5 或 BE-AW1 新接口。保留 React/TypeScript/Zustand/Radix/现有 Markdown 栈，不引入路由或查询框架。实施前重读 dirty diff，不能覆盖其他阶段改动。

## 2. 当前代码事实与差距

下面文件链接定位当前实现，函数名作为可检索锚点；结论针对上述 HEAD，不把方案中建议的组件当成已有文件。

| ID | 已核验事实与代码证据 | UI-4 决策 |
| --- | --- | --- |
| C4-01 | [AppShell](../../web/src/components/shell/AppShell.tsx) 的空间 Sidebar 仍是说明文字，主区挂载 `WorkspacePanel view="files"`；资料弹窗另挂一份 `view="authority"` | 替换空间 Sidebar/MainCanvas；空间、Picker、Inspector 共用查询缓存，避免各自加载全量树 |
| C4-02 | [WorkspacePanel](../../web/src/components/WorkspacePanel.tsx) 集中目录、搜索、上传、授权、影响确认和版本；`ordered` 展开整棵树，选择节点保存在组件中，未形成“目录导航＋直接 children 表格” | 分离目录与文件选择，目录由 route 驱动，文件由 Shell Inspector 驱动 |
| C4-03 | [InspectorHost](../../web/src/components/shell/InspectorHost.tsx) 的 file 分支只查 tree、显示名称和文件/目录，以及“在所属页面管理” | 新增 FileInspector；替换该分支，Host 不再重复 GET tree |
| C4-04 | `WorkspacePanel.useForConversation` 初值为 `true`；有活动对话时上传默认授权它 | 改为每次新上传默认仅保存；显式选择“同时供对话使用”及目标，不能隐式沿用后台 activeConversationId |
| C4-05 | `authorizeUpload` 与 `pendingAuthorization` 已支持保存成功后补授权；[现有测试](../../web/src/components/WorkspacePanel.test.tsx)覆盖不重复上传 | 保留语义，扩展到初始化、ready、节点保存各阶段恢复及不同目标的命令隔离 |
| C4-06 | `authorityScope/authorityRequest` 已覆盖部分 A→B→A；`selectNode` 中 retention/versions、搜索、published-files 等仍有直接 `.then(setState)` | 全部对象敏感查询补 account generation＋请求序号；不能把既有局部隔离当作完整保证 |
| C4-07 | [ConversationResources](../../web/src/components/conversation/ConversationResources.tsx) 已有内部 ResourcePicker、只读授权、部分撤销后读回以及操作 owner 隔离 | 提取并共享，不重写成简化版；保留[竞态与部分失败用例](../../web/src/components/conversation/ConversationResources.test.tsx) |
| C4-08 | [WorkResourcePanel](../../web/src/components/WorkManagement.tsx) 使用 Work resources/inputs；修改先读 Work，带 `If-Match`、命令 key；未消费撤权返回的受影响 Run 列表 | 共享表单，独立 Work adapter；保留 inputs 增删入口和 Work 条件命令，接入撤权状态 |
| C4-09 | [HpApi](../../web/src/api/resources.ts) 已有 tree/search/space/retention/versions/impact/上传/Conversation resources；无 node trace 封装，search 类型未声明后端已输出的 `created_at` | 补已有契约 DTO/wrapper；不能写成新增后端接口需求 |
| C4-10 | [catalog._tree_in_uow](../../src/workspace/catalog.py) 未输出节点时间或 content_type/work_id；[discovery.search/trace](../../src/workspace/discovery.py) 有创建时间、来源、历史使用与截断标志 | 行级信息使用真实已有数据；详情懒加载；无统一更新时间，无全主体授权反查 |
| C4-11 | [SaveWorkspaceDialog](../../web/src/components/TestPages.tsx) 已支持 ready file_id 直存和 HTML→text/plain→保存；缓存上传成功的 file_id，保存 key 随参数生成 | 提取公共保存组件及操作状态；保留两条链，补源对象切换/账户隔离；不删除整个 TestPages |
| C4-12 | [shell store](../../web/src/store/shell.ts) 已解析 dir/file Inspector；tab 类型目前仅 Run 三页签，origin 无 directoryId；[sessionLifecycle](../../web/src/store/sessionLifecycle.ts) 已统一 reset | 最小扩展按 kind 区分的 tab/origin 与纯 UI 状态；Workspace 查询/命令接现有同步 reset |
| C4-13 | [ApiClient](../../web/src/api/client.ts) 的 request 是 JSON 通道，已有 AbortSignal、session generation、401 处理 | 文本内容读取需补受同一生命周期管理的限量响应读取，不能直接把 attachment 接口当 JSON 或裸 fetch 绕过失效边界 |
| C4-14 | [文件服务](../../src/web_domain/file_services.py) 接受现有文本/PDF/DOCX/XLSX/PPTX；[content 路由](../../src/web_api/app.py) 强制 attachment/octet-stream | 按可信 metadata 决定文本预览；PDF/Office 下载与元数据可用，图片不新增支持 |

## 3. 目标结构与状态所有权

### 3.1 建议文件结构

以下是待新增/提取的组织方式，可合并小组件，但不可混淆状态所有权。

```text
web/src/components/workspace/
  WorkspaceScreen.tsx          # Header、面包屑、搜索、文件表格
  WorkspaceSidebar.tsx         # 全部文件与目录导航
  WorkspaceTree.tsx            # 仅目录层级及展开交互
  FileList.tsx                 # 表格与对象菜单
  FileInspector.tsx            # 文件四页签；目录分支仅适用信息和动作
  FilePreview.tsx              # 有上限的文本/Markdown预览
  FileVersions.tsx             # 历史、upgrade、已发布输出提交
  ResourcePicker.tsx           # 目录/文件选择和读取确认
  GrantEditor.tsx              # 主体、直接/继承规则和高级操作
  UploadToWorkspaceDialog.tsx  # 独立上传及显式可选授权
  SaveToWorkspaceDialog.tsx    # 既有输出/HTML文本副本保存
  WorkspaceMutationDialog.tsx # impact、确认、冲突反馈
web/src/store/workspace.ts      # 共享查询缓存、失效、请求协调、reset
web/src/store/workspaceUi.ts    # 纯UI状态与账户内草稿（也可合并为组件hook）
web/src/components/workspace/workspaceOperations.ts # 上传/保存意图与主体adapter
web/src/components/workspace/workspacePresentation.ts # 索引、路径、排序、继承展示
```

Shell 继续只有一个 InspectorHost；子组件不得再创建另一个并排 Inspector。目录详情可复用现有 `inspect=file:{nodeId}` 容器语义，通过 tree.kind 分支显示“目录详情”，不新增路由种类；目录不调用文件正文/versions/retention/trace 接口。

### 3.2 状态分工

| 状态 | 唯一所有者与键 | 生命周期 |
| --- | --- | --- |
| route、当前目录、Inspector 描述/返回栈 | Shell；沿用 dir 与 inspect | push 对象/目录导航；无效地址 replace；跨一级页面关闭当前面板 |
| 展开目录、排序、筛选、列表锚点 | Workspace UI；account＋视图 | 当前会话内保留，退出清空；不写权限或文件正文到 localStorage |
| tree、space | Workspace 查询 controller；account | 同一请求去重，首次进入空间/打开选择器才加载；多个消费者共享 |
| file metadata、正文、retention | account＋file_id；正文还带请求 token | 按需加载；关闭中止读取；切账户清正文并释放 Blob URL |
| versions、trace | account＋node_id＋当前 revision/file_id | 改名/移动刷新关联信息，内容版本变更失效；页签独立错误 |
| grants | account＋subject.kind＋subject.id | 直接规则缓存；继承仅从 tree 派生展示；后端判定最终权限 |
| 上传/保存/版本命令 | account generation＋operationId＋固定目标/payload | 一次意图保留各阶段结果和 key；UI 切对象不能改变已提交目标 |
| Run 状态 | 既有 Workbench/RunInspector/Works 优先 | 不把 Work Run 写入 activeRun，不新增 Chat SSE |

每次选择提交或查询刷新均递增 request token，即使 A→B→A 返回同一 ID，也不接受第一轮 A 的响应。所有 mutation 的 catch/finally 同样校验操作 owner，旧 A 不能解锁 B、清空 B 输入或把通知写到 B 上。

查询 controller 接 `sessionLifecycle.reset()`：先递增账户世代，再 abort 请求、清定时器/正文/草稿/命令恢复状态。切页可以停止视图查询；已被服务器接受的操作不能由“关闭面板”解释成取消业务。

## 4. 页面与交互设计

### 4.1 目录、表格与搜索

1. Sidebar：全部文件、根目录及子目录；展开状态独立于路由。采用原生层级按钮或完整可访问 Tree，避免只标 `role=tree` 却没有键盘行为。
2. 默认根目录，目录内只列直接 children；全部文件从全量树过滤 file。全部文件是 UI 筛选，首期保存在页面内存，刷新回合法目录，不用伪造 node_id 表达它。
3. 面包屑可导航祖先；文件点击只打开 Inspector，不改变当前目录；目录点击更新 dir，目录菜单可开详情。根节点禁用改名/移动/移除。
4. 表格为名称、类型、大小、来源。文件夹优先，名称稳定排序，同名用 node_id 决胜；类型无 metadata 时仅作扩展名提示，未知大小显示“—”。禁止逐行 GET metadata 造成 N+1。
5. 默认全空间搜索，提交一组过滤条件后固定 appliedFilters；已有 name/summary/content_type/purpose/work_id/source_run_id/from_date/to_date 均可在高级筛选使用。名称过滤不是任意二进制正文全文检索。
6. 分页以过滤指纹＋cursor 为键，追加按 node_id 去重，禁止同时请求相同页；新筛选递增世代、清 cursor，旧页不能混入；加载更多失败保留已加载结果和重试入口。
7. 搜索显示完整所在路径；search 返回而 tree 尚无对应节点时只刷新一次 tree，仍无法定位则显示位置待同步，不猜路径。目录内本地名称过滤必须标明范围，不能筛首批全空间结果来冒充全量目录搜索。
8. 404/已删除目录规范化回 root 并提示；点击缺失文件显示对象不可用，不自动换成邻近文件。200% 文字缩放下收起次要列，不靠横向缩小字号。

### 4.2 File Inspector

| 页签 | 查询与交互 | 空态/失败边界 |
| --- | --- | --- |
| 预览（默认） | file metadata 后按类型加载文本；Markdown 复用 react-markdown/GFM；下载 | 仅 ready 且类型/编码可信的文本；超过 1 MiB、未知二进制、PDF/Office 显示摘要与下载 |
| 详情 | 路径、大小、来源、retention；node trace 提供 run/work 回链；lineage 仅在展示来源链时按需读取 | 字段缺失不造值；子查询失败不抹去整个 Inspector；来源 Run 进入已有 RunInspector 内部返回栈 |
| 版本历史 | 当前 revision、历史文件下载、启用版本、选择已有 Run 输出提交 | legacy 不伪造版本；历史文件只读；错误保留已选输出，CAS 冲突提供另存 |
| AI 使用范围 | 明确所选 Conversation/Work，展示直接与祖先递归授权；历史 run_usage 单列 | 未选主体提示选择；不能显示“无人使用”；truncated 显示“仅展示部分历史”，不伪造更多页接口 |

文本读取遵循以下规则：

- 以 metadata.content_type/encoding/size_bytes 决定资格，不以下载响应的 octet-stream 或文件扩展名直接解码。
- 对已知超过 1 MiB 的文件不发正文请求；大小未知时首期只提供下载。即使 metadata 合格，读取流累计达到上限后也中止，避免先全量下载再截断。
- 内容方法使用同源认证、统一错误处理和账户 generation；401 进入统一会话失效。403/404 清当前正文与动作，网络错误可保留同一对象已加载内容并标“待同步”，不得保留上一对象正文。
- 普通 HTML 源码只显示文本；不启用 raw HTML、dangerouslySetInnerHTML 或 Artifact 的可执行 iframe。不能通过伪造 text/plain 接受图片上传。
- Tab 首次激活才请求，重复打开复用有效缓存；刷新重新验证对象。关闭恢复触发焦点，原行不存在则落到列表标题。

现有 Inspector.tab 需按 kind 扩展 File tabs（例如 preview/details/versions/usage），Run 的 overview/resources/advanced 不变。首期 tab 保存在内存即可，刷新回默认页签；不把主体权限、正文或敏感诊断放入 URL。origin 增 directoryId；同级文件替换当前顶层，File→Run 用内部有界返回栈。

### 4.3 在对话中使用

从行菜单或 Inspector 操作选择已有对话或新对话，展示目标标题与将授予的读取范围。已有对话列表复用会话分页；不自动发送消息。

新对话必须经现有会话创建能力显式创建一次，并保存得到的 conversation_id。若授权失败，保留该 ID 与 node_id，仅重新读 grants 后补缺失规则。授权成功才导航至目标 AI 上下文并聚焦 Composer；不能重新创建对话，也不能丢弃原目标。

已有待发附件时沿用 Shell 导航确认，不能绕过 UI-2 附件保护。无权/已删除对象使流程停留在明确错误态，不将未授权资料显示成已生效 Chip。

## 5. 上传、保存、影响确认与版本命令

### 5.1 上传与保存的恢复状态

建议将一次上传表示为 `idle → initializing → uploading → ready → saving → saved → granting(optional) → completed`，每一阶段可有 failed/unknown；这是前端操作状态，不是新领域状态机。

| 阶段 | 固定输入与保留结果 | 失败/响应丢失后的恢复 |
| --- | --- | --- |
| 初始化 | 文件、uploadKey；返回 file_id/content_url | 相同意图同 key 重试，禁止每次点击生成新 key |
| 内容提交 | 原 content_url、相同 bytes；ready file_id | 先查 metadata；ready 直接继续。仍 uploading 时按现有上传契约重试；过期/拒绝显示原因，新上传是新的显式意图 |
| 保存节点 | parent_id/name/file_id/saveKey | ready 内容不重传；原参数同 key 重试。改目录/名称是新保存意图；若原请求结果未知，先核实原结果再另存，避免无意重复入口 |
| 可选授权 | 已保存 node_id、显式目标 Conversation | 每次重试先查现存 grants，只补缺失操作；不重做上传或保存 |
| 结果通知 | 原文件名、原目标目录/主体 | 切换对象后仅更新原操作状态和目标缓存，不能显示为当前对象刚保存成功 |

新上传默认“仅保存到空间”，不因当前存在聊天对话自动授权；`file_upload` 能力不足时禁用上传并说明，已有文件仍可浏览。支持 MIME/扩展依据现有后端验证，不新增图片/FIG/text/html 承诺。

操作状态按账户放在 controller 中以承接关闭/重开，文件内容仅保留必要的 File 引用，不持久化到浏览器存储；刷新后不承诺恢复未完成的本地文件上传。可关闭已提交命令的视图时须明示“操作仍在处理中”，重开读取原操作，不再次提交。

提取 SaveToWorkspaceDialog：已有 ready 输出直接保存；HTML 文本副本沿现有 text/plain 链，不重新定义 Artifact MIME/provenance。UI-4 负责公共保存状态、目标目录、缓存失效与隔离；UI-6 负责 Artifact 版本选择及文案闭环。冲突“另存”为真正可操作的保存弹窗，不能只显示一条错误文字。

### 5.2 impact 与节点变更

- UI 将确认绑定 `{accountGeneration, nodeId, action, parentId, name, previewToken}`；改任一参数、切对象或刷新相关 tree 后使确认失效。注意后端 impact GET 只以 node 为参数，参数绑定由前端额外保证，不能描述成后端已经为每组目标参数签名。
- 改名/移动先 GET impact，显示 `potentially_affected_runs` 数量及可查看上下文；PATCH 沿用 body.preview_token。移除使用 DELETE 与 `X-Workspace-Preview`。
- 客户端过滤明显非法目标（自身、后代、根节点操作），最终以服务端冲突码为准；非空目录、Work 绑定输出目录等按真实原因解释。
- token 过期/冲突需重取预览并再次确认，不自动使用新 token 重放。无命令 key 保证的操作遇网络未知，先读 tree 核实结果，不宣称自动安全重试。
- “移除入口”不等于物理文件立即删除或释放容量。成功失效 tree/space/相关详情，关闭已移除对象或显示不可用，焦点回列表标题。

### 5.3 版本升级与 CAS

1. legacy 文件先显式 upgrade，保持 node_id；读回 current 版本后才允许提交。
2. 来源可选择/输入真实 Run，再 GET published-files；只从其返回输出选择 file_id，不把任意附件当已发布输出。切 Run 清旧输出选择，迟到列表不得覆盖新 Run。
3. 提交意图固定 node_id/run_id/file_id/expected_revision/expected_sha256/operationKey；原样重试复用 key。
4. `workspace_version_conflict` 保留输出 file_id 和用户输入，刷新最新 current，提供“将输出另存”或“基于最新版本重新确认”。重新确认是新意图和新 key，禁止静默更新 expected_revision 后自动覆盖。
5. 成功刷新 tree、versions、当前文件元数据/预览、trace；原历史 file_id 缓存仍按 immutable file_id 隔离。不改变 Task requirement 或接受成果状态。

## 6. 权限编辑与使用事实

### 6.1 共享界面、分别执行

定义显式 `ResourceSubject = { kind: 'conversation' | 'work'; id: string }`。GrantEditor/ResourcePicker 接收主体和 adapter，不读取隐式“当前对话”来决定提交目标。

| 契约 | Conversation | Work |
| --- | --- | --- |
| 查询 | GET conversations/{id}/resources，含 grants/attachments | GET works/{id}/resources；inputs 单独查询 |
| 新增 | POST resources，node_id/operations/recursive；不是 Work 条件命令 | 先取得真实 Work row_version，POST resources 带 If-Match 与幂等 key |
| 撤销 | DELETE resources/{grant_id}，返回 affected_runs 及 stop_state | DELETE resources/{grant_id}，带 Work 条件头/key，返回 affected_run_ids |
| 部分失败 | 读回当前 grants，重试尚缺/尚存规则 | 单主体修改串行，逐次使用真实返回/读回版本；冲突重查并确认，不重用旧版本连发 |
| 输入文件 | Conversation attachments 既有撤销能力保留，单列展示 | Work inputs 的增删及预算/条件命令保留，不能当成 node grant 删除 |

Work adapter 对应现有 [work resources 路由](../../src/web_api/app.py)和 [WorkIntegration grant_resource/revoke_resource](../../src/work_domain/integration.py)。共享的是呈现和操作结果结构，不能统一抹掉版本、幂等和响应差异。明确的 409 保留草稿；原响应未知时先查询或原样重放有保证的命令，不能立即换 key。

### 6.2 规则与主体选择

- 默认只选 list_metadata/read_content；create_child/update_content/delete_entry 放高级区且默认不选。目录 recursive 独立确认，文件不展示虚假递归选项。
- 根据 tree 祖先链及主体 grants 展示“直接”“继承自目录 X”；仅 recursive=true 的祖先规则可作继承来源。撤销继承必须作用到源 grant，确认影响该目录其他后代；不生成局部 deny。
- 文件所有者可浏览、主体授权、Run 冻结候选、Run 实际读取是四类事实。history.fixed_at/materialized_at/first_read_at 不能变成当前有效授权或完整“上次使用”统计。
- 主体选择器优先使用已有上下文/已加载对象，Conversation 可继续分页；Work 使用现有列表并明确“已加载任务”，支持按 ID GET 单对象验证和标题确认，不能暗示首批即全部。完整任务列表行为留 UI-5。
- 未选主体显示“选择对话或任务查看其权限”；Work 与 Conversation 不继承彼此 grants，link Work 不复制权限。
- 编辑未提交权限后切主体/对象/关闭时提供继续编辑或放弃确认；草稿按 account＋subject＋node 隔离，退出清空。

### 6.3 撤权与可见状态同步

多规则撤销按服务端读回收敛，失败仅重试仍存在的 grant。请求失败但读回已不存在可确认撤销；读回也失败则显示状态待确认，保留原目标和动作上下文。

`affected_runs.stop_state=stopping` 或 Work 返回的 `affected_run_ids` 只表示需跟进，不能直接写“已停止”。优先复用已在观察的 Run 状态；其余受影响 Run 采用去重、有限并发的 snapshot 查询（可沿用 2 秒基准、网络失败退避），到终态停止；页面隐藏暂停展示轮询，恢复后读回，退出清理。查询失败持续显示未确认，不靠超时假定终态。

授权/撤权后失效主体 grants、Composer Chips、相关 Run resources；Work 操作更新对应 Work 真相。撤权涉及当前查看的诊断时同步失效受影响 Model Input 查询/正文，复用 UI-3 的权限拒绝清理逻辑，不能重新展示已撤权缓存。新增授权只承诺下一轮可用，不重写当前 Run 冻结候选。

## 7. API 补全与缓存失效清单

以下路径均以 `/api/v1` 为前缀，均为已有后端能力。

| 项目 | 前端动作 | 实施约束 |
| --- | --- | --- |
| GET workspace / search / space | 抽命名 DTO，补 search.created_at 等真实响应字段 | tree 不增虚构时间、MIME、work_id；创建时间与版本时间命名分开 |
| GET workspace/nodes/{id}/trace | 新 HpApi wrapper 与 DTO | 包含 run_usage_truncated；仅文件查询，不当授权反查 |
| GET files/{id} / content / lineage | 复用 metadata，新增受控文本读取；按需补 lineage wrapper | 现有 JSON request 行为保持，内容流支持 abort/限量/统一 401 |
| GET/POST versions、POST upgrade | 复用 wrapper，显式意图与失效 | revision/sha/key 都保留；不强转空 revision |
| GET impact、PATCH/DELETE node | 复用原 token 契约 | 不发明命令幂等或自动恢复 API |
| Conversation resources / attachments | 提取既有控制逻辑 | 保留分页候选与原附件能力；不混用临时附件移除 |
| Work resources / inputs | 将现有 raw request 提取为 typed adapter | If-Match、key、affected_run_ids 完整消费，Work 控制逻辑不迁入 Workspace |

最小失效规则：

| 成功事件 | 失效/刷新对象 |
| --- | --- |
| 上传/保存节点/新目录 | tree、space、当前搜索结果标记过期、目标 file/node；保持用户目录和滚动 |
| 改名/移动/移除 | tree、路径、受影响目录/后代继承展示、node 详情、search；移除后清选中正文 |
| 新版本/upgrade | tree、node versions/trace、当前 file/retention/preview、search |
| grant/revoke | 指定主体 grants、对应 Chips；受影响 Run resources/诊断与真实状态；Work mutation 对应 Work |
| 跨账户/退出 | 所有查询、操作草稿、未完成恢复指针和正文；迟到回调全部失效 |

不做每次 mutation 全应用 reload；search 修改后显示“结果已变化，请刷新”或刷新首批，不能把新首批直接接旧 cursor 继续拼接。

## 8. 分步实施与交付顺序

| 步骤 | 工作与预计主要文件 | 退出检查 |
| --- | --- | --- |
| U4-01 契约与基线 | 核验 HEAD/diff；盘点 WorkspacePanel、WorkResourcePanel、ConversationResources、SaveDialog 入口；补 types/resources wrappers 和 adapter 契约 | 类型与 API 测试证明字段/头/响应差异；形成旧能力→新入口表 |
| U4-02 查询与操作生命周期 | workspace controller、操作 intent、sessionLifecycle；tree 索引和纯投影；文本读取最小扩展 | 同请求去重、A→B→A、退出清缓存、原意图重试和局部失效测试通过 |
| U4-03 空间浏览 | Sidebar/Tree/FileList/Search、AppShell/shell 接线 | 目录 URL、children/全部文件、搜索分页、文件开唯一 Inspector、空态/长标题通过 |
| U4-04 文件阅读 | FileInspector、Preview、详情/trace/retention、历史只读 | 文本上限与安全、二进制降级、四页签局部错误、Run 回链/返回通过 |
| U4-05 写入与版本 | Upload/Save/MutationDialog、FileVersions、公共保存入口替换 | 默认不授权、所有部分成功恢复、impact 失效、CAS 冲突另存与焦点通过 |
| U4-06 权限整合 | 提取 ResourcePicker/GrantEditor、ConversationResources、WorkResourcePanel；在对话中使用 | 两主体不串、继承撤销、inputs 原动作可达、新对话只创建一次、受影响 Run 与诊断失效通过 |
| U4-07 回归与交接 | 清本阶段旧调用点、保留必要薄兼容层；测试、截图、报告、索引 | 下述矩阵无未说明失败，所有删除能力有新入口，提交 UI-4 实施报告 |

这些步骤是同一 UI-4 阶段的顺序工作包，不要求并行改同一模块。U4-03～06 可逐步整合，但在新入口完整前不能删除原能力。跨阶段代码只做必要接线，不提前重做 UI-5/6。

删除条件：`WorkspacePanel` 所有业务分支已有对应新组件、原测试场景迁移后，才删除或退化为无查询/无状态的兼容壳；`TestPages` 的 Artifact/诊断其他导出留后续阶段；`WorkResourcePanel` 仅替换资源表单，保留输入引用和未迁移任务功能。

## 9. 验收矩阵

所有项目均为后续执行要求，本计划不声称通过。

| ID | 场景及必须断言 | 验证层 |
| --- | --- | --- |
| U4-A01 | 根目录/嵌套目录/全部文件/面包屑、刷新深链、浏览器返回；只列直接 children，未知目录回 root | 单测＋E2E |
| U4-A02 | 全空间搜索多页；修改条件时旧页迟到；失败重试不丢结果/不重复项；不伪造更新时间 | controller＋组件 |
| U4-A03 | 文件打开/兄弟切换/Run 子对象返回；唯一 Host；文件与目录分支不误请求 | 组件＋E2E |
| U4-A04 | 文本/Markdown/HTML 源码、恰好 1 MiB/超限/未知大小；PDF/DOCX/XLSX/PPTX 摘要下载；无脚本执行 | 组件＋API＋E2E |
| U4-A05 | 无对话也可上传；有活动对话默认仍不 grant；上传能力关闭时入口准确 | 组件＋E2E |
| U4-A06 | 初始化/PUT ready/保存响应丢失与重试；内容只传一次，原 key/payload 稳定；换目标不误写 | mock 契约＋API |
| U4-A07 | 保存成功授权失败只补授权；原对话切换/关闭重开/账户切换后结果不串 | 组件＋E2E |
| U4-A08 | file_id 直存与 HTML 文本链；保存失败保留 ready；跨对象迟到不关闭新弹窗 | 组件＋artifact/Run 回归 |
| U4-A09 | impact 后改 name/parent/action/node、预览过期、网络未知；都不自动危险重放 | 组件＋真实 API |
| U4-A10 | 根节点、非空目录、自身/后代目标、绑定 Work 输出目录；移除只承诺入口语义 | API＋E2E |
| U4-A11 | legacy upgrade、下载历史、不同 Run published-files 迟到、CAS 两端竞争、冲突输出另存 | 单测＋API＋E2E |
| U4-A12 | Conversation/Work 直接与递归继承；高级写权限默认未选；撤销源规则而非伪 deny | 纯投影＋API＋组件 |
| U4-A13 | 两主体接口/If-Match/key 不混用；Work 多规则修改版本连续；409 保留编辑内容 | adapter＋真实 API |
| U4-A14 | 多规则部分撤销、DELETE 响应丢失、读回也失败；重试仅存活规则；stopping 到终态前不称已停止 | 组件＋真实 API |
| U4-A15 | history 截断/空历史/未选主体；不推导完整有效授权；Work inputs 与 Conversation attachments 原动作仍可达 | 组件＋回归 |
| U4-A16 | 在已有/新对话使用；授权失败不重复建对话；成功聚焦 Composer、不自动发送；保留附件导航保护 | 组件＋E2E |
| U4-A17 | metadata/正文/retention/versions/trace/search/grants 的 A→B→A；跨账号迟到、401 清理；旧 finally 不解锁新操作 | controller＋session 回归 |
| U4-A18 | 撤权使受影响 Run resources/Model Input 失效；Work Run 不写 Chat activeRun；不重复订阅 SSE | run/trace/workbench 回归 |
| U4-A19 | Tree/表格/Tabs/菜单全键盘、焦点返回、移动 modal inert、确认弹窗 Esc 层级、草稿放弃保护 | E2E＋人工键盘 |
| U4-A20 | 桌面/移动多尺寸、200% 文本缩放、长中英文名、窄高窗口、reduced-motion；无重叠/横向溢出 | 截图＋人工 |

对应重构方案：A01～04→§5.1/5.3、D03/D07；A05～11→§5.2、D08/D09；A12～18→§5.3/9、D08/D09、FE-B1/FE-B3 的回归；A19～20→§12。T/M 任务映射向量归 UI-5，本阶段不声明已覆盖。BE-* 不作为隐藏的通过项。

### 9.1 建议测试文件与命令

保留并迁移 `WorkspacePanel.test.tsx`、`ConversationResources.test.tsx` 中已有用例语义，新增 workspace controller、FileInspector、GrantEditor、Upload/Save/Mutation、versions 的针对性测试；新增 `web/e2e/ui-4-workspace.spec.ts`。不以修改测试文案替代业务断言。

实施后的前端门禁（在 `web/` 下）：

```bash
npm run typecheck
npm run lint
npm run build
npm test
npm run test:e2e -- e2e/ui-4-workspace.spec.ts e2e/workspace-direct-upload.spec.ts e2e/workspace-p1.spec.ts e2e/workspace-p3.spec.ts e2e/manual-repair.spec.ts e2e/ui-2-ai.spec.ts e2e/ui-3-execution.spec.ts e2e/artifact.spec.ts e2e/auth.spec.ts e2e/multi-tab.spec.ts e2e/a11y.spec.ts
```

后端契约复验（仓库根目录；先配置隔离环境）：

```bash
PYTHONPATH=src python3 -m pytest test/web_api/test_workspace_direct_upload.py test/web_api/test_workspace_v41_p1.py test/web_api/test_workspace_v41_p2.py test/web_api/test_workspace_v41_p3.py test/web_api/test_work_foundation.py test/web_persistence/test_workspace_v41_p1.py test/web_persistence/test_workspace_v41_p2.py test/web_persistence/test_workspace_v41_p3.py test/web_persistence/test_resource_account_integrity.py -q
```

以上路径均已核验存在，UI-4 新测试文件为待新增。真实撤权/工具执行覆盖按改动复用 `test/web_persistence/test_workspace_temporal_revoke_tool.py`；若未运行独立 Temporal 环境，明确记为未覆盖，不能拿 Fake Executor 等价替代。

环境按[测试指南](../development/testing.md)配置：专用可丢弃 PostgreSQL（migration/API/worker 同库不同角色）、独立 Redis DB/文件目录/端口；fixture 会清表，不用业务库。Playwright 保持 workers=1，沿用真实 API＋PG＋Redis＋Fake Executor。长命令遵守 AGENTS 长等待策略，不反复短轮询报告“仍在运行”。

截图至少覆盖 360×800、390×844、768×1024、1024×768、1280×800、1440×900、1920×1080；各主要边界覆盖列表、Inspector 打开/关闭、空态/错误、长标题、授权确认。用例记录必须区分浏览器自动化、人工键盘、真实基础设施，不将截图视为无障碍认证。

## 10. 风险、剩余增强与交接

| 风险/增强 | UI-4 处理与边界 |
| --- | --- |
| 全量树成本与重复取数 | 单处缓存、线性索引、可见目录展开、按页签读取；用 1k/10k 树夹具记录渲染耗时和请求数，禁止每行取 metadata；不宣称服务端懒加载。BE-W3 分页节点留后续 |
| 文件全主体使用范围 | 当前所选主体管理＋历史使用；BE-W1 有效主体反查留后续 |
| 聚合详情与统一更新时间 | 分开查询和局部错误；BE-W2 聚合/真实时间投影留后续 |
| 回收站/恢复 | 不展示空入口；BE-W4 单独设计 |
| 图片/额外类型 | 保留现有 MIME 白名单；BE-W5 单独增强 |
| Artifact 一等保存与来源 | 保留 HTML 文本副本链；BE-AW1 不纳入 UI-4 |
| UI-3 修复被回归 | 保留终态输出刷新、权限拒绝清 Model Input、可见性轮询与懒加载测试；不能以“非本阶段”删断言 |
| 公共保存组件跨阶段 | UI-4 提取和稳定契约；UI-6 接入完整 Artifact 交互，移除前验证所有旧调用点 |

交付材料建议：

- `docs/implementation/ui-4-workspace-permissions-report.md`：最终 HEAD、文件清单、C4/U4-A 与基线 D/FE-B 对照、实际命令/结果、已知失败和未覆盖环境。
- `artifacts/product-acceptance/ui-4/README.md`：日志、截图和成功/失败恢复/并发迟到三条操作轨迹；日志脱敏，不保存账号凭据或文件正文。
- 完整旧入口→新入口表：空间浏览、搜索、上传、Conversation grant/attachment revoke、Work grant/inputs、Run 候选资料、保存输出/文本、impact、版本升级/提交/下载全部可达。
- 后续 UI-5/UI-6/UI-7 的接入说明：共享主体 adapter、SaveToWorkspaceDialog、File Inspector origin/tab、缓存失效 API；BE-* 保持独立编号。

退出条件：矩阵对应证据完整，无未处理的阻断性权限/数据丢失/账户隔离回归；前端门禁和受影响 E2E/后端契约通过，未覆盖的真实环境如实登记。UI-4 完成不等于整个新 Shell 可最终发布，仍服从方案 §14.4 的全能力发布门槛。

回退仅回退本阶段前端视图/接线提交或使用开发期兼容入口，不清服务器对象、不撤销用户 grant、不反向改 Work 状态、不回滚文件版本；没有数据库迁移。若实现中发现必须新增后端契约，单列 BE 变更并明确阻断的具体体验，不悄然扩大 UI-4。
