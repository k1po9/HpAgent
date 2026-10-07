# UI-4 空间与权限实施报告

> 后续自检与修复（2026-10-07）：[UI-4 自检记录](ui-4-self-review.md)的 R1～R4 已修复，新增14项正式回归。下文244/37/30保留为原实施历史证据；本次修复验证单独记录于文末及自检证据目录。

日期：2026-10-07（Asia/Shanghai）。实施基准 HEAD：`40a318a5600a3123f3149e2788ddafaf28d59e9c`。依据 [实施计划](ui-4-workspace-permissions-plan.md) 和用户提供的 v1.0 补充设计。开始时已有 `docs/implementation/README.md` 修改及未跟踪的 UI-4 计划；保留这两项，不把附件中的历史“仅制定计划”说明当成本次实施限制。本批交付包含原 UI-4 实现及本次自检修复，提交主题为 `feat(web): implement UI-4 workspace and fix review findings`。

本阶段将空间浏览拆成目录导航、直接子项表格和唯一 File Inspector；上传默认仅保存，授权需要显式目标。复用原有 API、Work 条件命令、Workspace impact/CAS、会话 store 与 SSE；没有后端领域、数据库迁移或新能力接口变更。

## 实现与原能力入口

| 原能力 | 新入口及实现 |
| --- | --- |
| 全量树、目录导航 | `WorkspaceSidebar` / `WorkspaceScreen`；根目录与 `dir` 深链、祖先面包屑、独立展开与全部文件；文件夹优先稳定排序 |
| 文件搜索 | MainCanvas 全空间名称＋高级过滤；固定 appliedFilters、cursor 分页、去重与旧响应隔离；变更后提示刷新 |
| 文件详情、下载 | `FileInspector` 的预览/详情/版本历史/AI 使用范围四页签；目录只调用适用信息；来源执行用 Host 返回栈打开 |
| 正文 | 可信 ready/UTF-8/已知大小元数据门控；统一认证客户端最多读取 1 MiB；Markdown 无 raw HTML，HTML 只显示源码；其余摘要与下载 |
| 独立上传 | `UploadToWorkspaceDialog`；固定一次意图、上传/ready/保存/可选授权分别恢复；会话内关闭重开查看原操作 |
| 输出和 HTML 文本副本保存 | 公共 `SaveToWorkspaceDialog`；ready file_id 直存与现有 text/plain 链；源对象隔离；明确冲突后保留 ready 内容更换保存意图 |
| 改名、移动、移除入口 | File Inspector 的管理弹窗；impact 绑定对象/参数/tree revision；确认过期重取且再次确认；未知网络结果先核实，不自动危险重放 |
| legacy upgrade、版本提交 | 版本历史页；真实 published-files 选择，revision/sha/key CAS；冲突保留输出并打开实际另存弹窗 |
| Conversation 授权 | Composer 共享 `ResourcePicker`，对话资料 `ResourceManager` 与文件使用范围 `GrantEditor`；读取默认、写权限高级区、递归显式选择 |
| Work 授权与 inputs | 复用同一 `GrantEditor` 的独立 Work adapter，保留 `If-Match` 与原意图 key；任务页工作资料与输入引用增删入口 |
| Conversation 附件撤销 | 对话资料中的附件可用性单独管理，不混用 grant 删除 |
| Run 候选/固定/读取 | 从对话资料打开既有 Run Inspector 资料页；权限变更刷新该页，保持单一 Chat feed |
| 在对话中使用 | 选择已有或显式新建目标；新建 key/ID 与待授权恢复指针保留；授权成功才导航，既有附件离开确认继续生效，无自动发消息 |

`WorkspacePanel` 暂留作迁移比较和已有回归测试，业务 Shell 已移除其两个挂载点；`TestPages` 保留 Artifact/诊断导出，保存弹窗改为公共组件兼容导出。没有提前重做 UI-5/6/7。

## 生命周期与权限约束

- Workspace 查询按账户世代共享缓存；tree 请求去重，metadata/版本/trace/retention/lineage 按需加载；hook 按当前 key 订阅共享 cache，正文读取保留独立 view token。无逐行 metadata 请求。
- 搜索条件与分页请求有独立世代；新筛选/变更/退出拒绝旧页，分页失败保留已有结果。
- 上传与保存命令固定原目标和 key；内容响应丢失先查 ready；保存响应未知复用原参数/key；已保存而授权失败只补缺失规则。恢复记录仅在当前账户内存，不写 localStorage。
- 继承展示只取祖先 recursive 规则；撤销继承作用于源规则，不生成局部 deny。历史候选/读取与当前所选主体授权分别展示，截断历史不伪造分页。
- 多规则撤销通过权威读回判断剩余规则；Work 命令串行使用最新版本，未知响应保留原 key/版本；409 保留编辑。
- 受影响 Run 去重、最多三路 snapshot 查询，非终态显示正在停止，失败显示待确认，页面隐藏暂停；同步清除 Model Input 缓存。Work Run 不写入聊天 activeRun。
- 未提交权限选择离开页面/关闭 Inspector/切主体时有继续编辑或放弃确认；同步 session reset 清查询、草稿、恢复指针与受影响 Run 定时器。

## 验证证据

详见 [证据目录](../../artifacts/product-acceptance/ui-4/README.md)。后端契约使用可丢弃库 `hpagent_ui4_test_20261007`，浏览器前三轮使用独立库 `hpagent_ui4_e2e_20261007`，最终使用全新库 `hpagent_ui4_e2e_final_20261007`，同库真实 migration/API/worker 角色；Redis DB 12、API 8184、Vite 5277、独立文件目录 `/tmp/hpagent-ui4-files-20261007`。凭据仅存临时 0600 文件，不收录于报告。

前端静态门禁 `npm run typecheck`、`npm run lint`、`npm run build` 全部通过。构建保留 Vite 大于 500 kB 的 chunk 提示，没有构建错误。完整 Vitest 使用 `npx vitest run --maxWorkers=1 --testTimeout=15000`：35 个文件、244 项通过（100.64 秒）；单 worker 与较长测试超时用于避开本机基础设施并行运行时的资源竞争，不修改业务断言。计划中后端命令通过 `.venv/bin/python` 执行：30 项通过、1 项 Starlette/httpx 弃用提示，788.02 秒。完整 Chromium 矩阵在最终全新可丢弃库上 **37 项全部通过（13.8 分钟）**，使用计划列出的全部 11 个 spec、workers=1。第三轮复用库 36 通过、1 个 Artifact queued 超时；最终新库包含 Artifact 生成/交互/下载/保存/刷新恢复验证。未扩大到后端 Fake Executor 改造。

首轮完整单测有三项 5 秒超时，首轮浏览器 30 通过、6 失败，原始日志保留。复验前修复：当前 Rail 再点击保留当前目录、目录 Inspector 标题测试适配、旧资料入口断言迁移、上传记录切换同步原操作指针。第二轮浏览器 35 通过、2 失败：文件专用焦点回退误用于 Run，已限定为 File；双账户用例完成两轮业务断言后在 context teardown 前耗尽 60 秒总预算，单独标记 slow 后复验，断言不变。撤权 snapshot 另外补足在途 ID 去重，并加入隐藏暂停/三路并发上限/跨账户迟到测试。源文件热更新期间出现 DOM detached；其他首轮超时/空页面与资源竞争同批发生，仅最终稳定代码复验作为完成证据，不把失败记录删除。

### U4-A 验收对照

下表记录自动化证据涉及的主要断言。一个编号包含多个边界，自动化通过不表示穷尽其所有人工或真实执行环境。

| ID | 已实现与证据 | 边界 |
| --- | --- | --- |
| U4-A01 | UI-4 E2E：嵌套目录深链刷新、直接 children、全部文件、面包屑、浏览器返回、未知目录归根；`WorkspaceScale` 验证目录投影 | 全量树接口保留 |
| U4-A02 | `workspace.test.ts`：旧筛选迟到、cursor 多页去重、分页失败保留结果、重复 load-more 锁与变更 stale | 搜索使用真实字段，未伪造更新时间 |
| U4-A03 | `FileInspector` 目录不请求 file API；Shell/Run Inspector 返回栈回归；E2E 唯一 Host、开关/返回 | tab 内存保存，URL 只写对象 ID |
| U4-A04 | `FileInspector` + client：1 MiB 边界、未知大小/二进制降级、HTML 无可执行元素、A→B→A 正文；E2E Markdown；既有 API 下载 | 不引入图片预览或办公文件内嵌解析 |
| U4-A05 | `workspaceOperations` 默认无 grant；UI-4 和 direct-upload E2E 验证无对话/活动对话保存；上传能力由现有 capability 门控 | 没有全局授权 |
| U4-A06 | 操作测试：内容响应未知查 ready、保存同 key/payload 重试、双击锁、世代重置；真实 API 幂等；E2E 丢失已落地 PUT 响应仅传一次 | 未知结果不允许改原目标 |
| U4-A07 | 操作测试只补缺失授权；E2E 保存后 grant 失败、关闭重开仅补 grant；session 回归清恢复记录 | 恢复指针仅当前账户内存，刷新页面不承诺持久恢复 |
| U4-A08 | `WorkspaceCommands` 跨源迟到不关闭新弹窗；workspace-p1 的 ready file_id 保存、artifact 既有文本保存回归 | HTML 保留 text/plain 副本链 |
| U4-A09 | `WorkspaceCommands` 参数变化/预览过期均需要再确认；UI-4 E2E 修改名称使确认消失；真实 API topology/policy 过期拒绝 | 未知网络结果先刷新核实 |
| U4-A10 | 后端 P1：根/非空目录/跨账户/循环保护，并发移动；前端排除自身及后代，服务器继续检查绑定输出目录 | 移除入口不是立即物理删除 |
| U4-A11 | workspace-p3 E2E：upgrade、版本历史及旧版下载；组件：换 Run 迟到、CAS 冲突真实另存；持久化 P3：跨 Conversation 竞争、published recovery | 未改后端 CAS 协议 |
| U4-A12 | `FileInspector`：只继承祖先 recursive，写权限默认未选；现有 API Conversation/Work 规则与撤销；`GrantEditor` 删除源规则 | 无局部 deny |
| U4-A13 | adapter 测试：Work 独立端点、If-Match/key 未知重试；真实 P2 Work 版本/重放；命令按 Work 串行 | 409 保留编辑，明确重试才读取新版本 |
| U4-A14 | DELETE 丢响应权威读回测试；原 ConversationResources 部分失败/读回失败回归；后端 P2 实际撤权阻断与取消状态 | 新增快照回归验证在途去重、隐藏暂停、最多三路、跨账户迟到及终态后停止轮询；真实 Temporal 尚未运行 |
| U4-A15 | usage 区展示当前所选主体与历史截断提示；保留 Work inputs、附件撤销和 Run resources 原入口；相关 store/组件回归 | 不推导全主体有效授权，历史没有假分页 |
| U4-A16 | UI-4 E2E +组件：新对话创建一次、失败只重试授权、无 messages POST；既有附件离开保护回归 | 授权成功才导航，Composer 聚焦 |
| U4-A17 | shared query/controller、正文、published outputs、保存窗口及现有 session/client 回归；auth/multi-tab 浏览器隔离 | 缓存/session 世代与 view token 各自检查 |
| U4-A18 | 原 RunResources/traceStore/RunInspector/App feed 回归；权限变更刷新 candidates 与清 Model Input；Work adapter 使用独立 snapshot | Fake Executor 不等于真实执行停止 |
| U4-A19 | UI-4 E2E：页签方向键、权限草稿继续/放弃、焦点返回、Esc；既有 a11y：移动 focus trap/inert | 人工辅助技术/软键盘未覆盖 |
| U4-A20 | 7 种尺寸列表及 Inspector 截图，长中英文名、空/错误态、reduced-motion、200% 文本大小，横向溢出断言 | 截图不能替代人工无障碍认证 |

### 补充方案对照与文件清单

C4/UI-4 对应目录/文件列表、File Inspector、Workspace 写入与权限接线，完成内容见上表。D03 固定四区由新 Sidebar/MainCanvas 承载；D07 使用同一 InspectorHost 和内部返回栈；D08 保留 API、SSE、Work、Workspace、Artifact 原领域与幂等/CAS；D09 上传/保存与显式授权分开。FE-B1 复用同步 session reset 与世代隔离；FE-B3 File/Run/Artifact 使用单一 Host，权限刷新不新建 Chat SSE。

完整源码与测试清单见 [changed-files.txt](../../artifacts/product-acceptance/ui-4/changed-files.txt)。审查与实施基线为上述 HEAD，实际交付提交由 Git 历史定位；包含 `versionOperations.ts` 和正式自检回归，原输入计划及自检文档一并保留。

主要文件：

- `web/src/components/workspace/`：Sidebar、Screen、FileList、Inspector、Preview、Versions、Mutation、Upload、Save、ResourcePicker、ResourceManager、SubjectPicker、GrantEditor、UseInConversation、查询 hook/操作控制器/索引，以及四类针对性测试。
- `web/src/store/workspace.ts` / `workspace.test.ts`：共享查询、树与搜索；`shell.ts` / `sessionLifecycle.ts`：路由、草稿离开保护与重置。
- `web/src/api/{types,resources,client}.ts`：DTO、原 API wrapper 和有限文本读取，及 client 单测。
- Shell、InspectorHost、WorkManagement、ConversationResources、RunResources、RunInspector、TestPages、styles：新入口接线及必要兼容；App/WorkspacePanel 旧测试保留业务断言。
- `web/e2e/ui-4-workspace.spec.ts` / `workspace-helpers.ts`，及 direct-upload/P1/P3/manual-repair 原用例迁移。
- 本报告、实施索引与 UI-4 验收证据；原 UI-2/UI-3 截图不作为本阶段变更交付。

## 范围与后续接入

UI-5 可复用 Subject/Work adapter 与输入引用呈现；UI-6 使用 `SaveToWorkspaceDialog` 和固定操作恢复状态；UI-7 可在全部旧能力回归后删除旧 `WorkspacePanel`。File tabs 仅在内存中保存，URL 保持节点与目录标识，不写正文、权限规则和诊断。

BE-W1 有效主体反查、BE-W2 聚合详情/真实更新时间、BE-W3 服务端分页树、BE-W4 回收站、BE-W5 图片/额外类型、BE-AW1 Artifact 一等保存仍独立排期；本阶段无假更新时间、图片上传、回收站或完整全主体授权声明。

真实 Temporal 撤权执行、真实模型/渠道、人工辅助技术及手机软键盘未作为自动化证据覆盖。Fake Executor 浏览器结果不能替代这些环境，也不构成 WCAG 合规认证。UI-4 不等于整个新 Shell 已满足最终发布门槛。

## 自检修复后的验证

R1～R4 的原因、实现及正式反例见 [自检修复记录](ui-4-self-review.md)。共享授权查询在多消费者/StrictMode/刷新替换后收敛到同一有效 cache；版本命令跨页签/关闭保留同一冻结 CAS/key；已确认撤权即使后续读回失败或视图切换也使同账户缓存失效；合法面包屑不再误报，草稿阻止归根时不提前提示。对应 U4-A01/A11/A12/A14/A17/A18 补验，不改写原有历史计数。

本次 `npm run typecheck`、`npm run lint`、`npm run build` 均通过；lint 无警告，构建仍有既有大 chunk 提示。完整 `npx vitest run --maxWorkers=1 --testTimeout=15000`：36文件、258项通过，91.68秒；[fix-unit.log](../../artifacts/product-acceptance/ui-4/self-review/fix-unit.log)。后端定向6项通过，含真实 API 相同版本 key 重放后历史数量不增加；未重新宣称原30项均在本次重跑。浏览器完整矩阵37项通过（5.6分钟），见 [fix-e2e.log](../../artifacts/product-acceptance/ui-4/self-review/fix-e2e.log)。之后补同账户认证投影刷新分支，完整前端门禁再次通过，并单独复验账户浏览器场景；顺序及独立结果见自检文档，不累加重复用例。
