# UI-6 HTML Artifact 实施报告

> 修复复验（2026-10-08）：自检 R1/R2/R3 已修复，原 4 个反例归入正式回归并通过；修复说明及独立验证见 [UI-6 自检与修复记录](ui-6-self-review.md)。下文原实施批次计数仍保留历史含义。

日期：2026-10-08（Asia/Shanghai）。实施基线 `9560c99675d6ebfd159909ea9d812790572c6a1d`；开始时已有 `docs/implementation/README.md` 修改及未跟踪的 UI-6 计划文件，已保留。依据 [实施计划](ui-6-html-artifact-plan.md)；附件作为产品设计补充，本次用户授权实施 UI-6，计划中上次“仅制定文档”的范围说明保留为历史。

## 实现结果

AI 完成且正文非空的回复展示全部已有 HTML 对象及显式生成入口；首次创建、再次生成和修改均有对象内提交锁与固定幂等意图。消息可见时查询，按消息去重且最多四个并发；失败显示重试，不解释为空列表或自动创建。账户切换使查询队列、请求、轮询、草稿和操作意图失效。

Artifact 进入共享 Surface 的专用 Inspector，提供预览、版本历史、详情。Shell 是对象/版本/tab 选择的唯一权威，默认按真实数值选择最近成功版；显式缺失版本显示 unavailable，没有静默替换。切版本不卸载修改区，版本完成只更新缓存、历史和消息摘要，提供主动查看入口。历史展示实际 version、parent、instruction、时间、状态；详情展示来源、真实 producing Run 和失败代码。

草稿按 Artifact 保留在账户内存中，关闭/切版可恢复。修改前刷新版本，禁止未完成构建叠加，核对最近成功 parent；请求只发送 instruction，服务端 parent 是事实。指令按 Unicode code point 计数，允许 1–4000 字符。IME 中不提交，Enter 保持 textarea 换行。失败保留草稿；网络/5xx 不确定时恢复固定 key 和原指令，清空仅发生在确认成功且原草稿 revision 未改变时。草稿 revision 也随不确定意图保留，恢复不会清除后来编辑的新文本。

重开恢复所有 queued/running 版本，消息行发现构建也接管轮询，沿用每版一个 poller 与 1/2/3/5 秒退避，单版本 GET 最多四个并发。网络中断只更新同步错误，不修改 build status；403/404 清对象正文、版本、命令和摘要，并终止该对象失效轮询。GET 发出序号按版本合并，completed 不回退，较新 failed→running 重试仍接受；列表不因其他版本写入而回灌整份缓存。对象 GET token 和账户 generation 隔离迟到结果，已接受 POST 只回写原对象，页面离开后不自动打开或切版。

预览仍使用 `srcDoc` 与 `sandbox="allow-scripts"`，保持 event.source 校验；HTML/版本切换重建 iframe 并清运行错误，旧 iframe 消息无效。下载冻结所选成功版，用 `text/html` Blob 和清理后的 `标题-vN.html`，定时、卸载、退出释放 Object URL。

空间保存直接接入正式 SaveToWorkspaceDialog，冻结 HTML、标题、Artifact/version 身份，默认 `标题-vN.html.txt`，上传 MIME 为 text/plain。复用原 UI-4 的 ready metadata、file_id、上传/save key 与恢复链。409 改目标保留源说明和已就绪内容；成功文案区分源码副本，返回真实 node_id 的空间入口。关闭后可从现有上传与保存记录恢复，记录也显示源码版本及空间回链；已有 file_id 保存调用保持可用。

Task 来源带原 version/revision 与稳定焦点触发标识。刷新 Task 深链只查询对应 Work，匹配真实引用；多引用且无法唯一确定时显示来源待核实，不反查全账户。手工 v2 不替代原交付，不增加接受最新版入口；返回现有 Task 成果页，沿用 UI-5 验收资格和命令。

## 范围与文件

对应 D01/D05/D07/D08/D09/D10、FE-B1/FE-B3/FE-B5，完成 U6-0～U6-5 接线并执行 U6-6 自动化验证。未改变 Run/Work/Artifact/Workspace 持久化模型、SSE、API 请求契约或验收资格。

- 新增：`components/artifact/ArtifactInspector`、`ArtifactComposer`、`ArtifactMessageItems`、下载/展示辅助函数与定向测试；`store/artifactUi`。
- 迁移：`store/artifacts` 查询/命令层、Shell 路由与焦点、sessionLifecycle、InspectorHost/AppShell、HpThread/ChatPane、TaskOutputs、临时 TestPages/ArtifactPanel。
- 最小增补：Artifact DTO producing_*；SaveSource、保存对话框、保存记录和 workspaceOperations 的源冻结/来源反馈；ArtifactPreview 预览生命周期；本模块 CSS。
- 验证：原 Artifact/Shell/App/Preview/Workspace/Thread 测试、新组件测试、Artifact E2E 和 `ui-6-artifact.spec.ts`；`test/web_persistence/test_work_integration.py` 补真实 v2/验收拒绝契约，`test/web_api/test_work_foundation.py` 补 accept-result 过期 If-Match 的 HTTP 409 与无写入断言。

完整工作区文件清单与逐项覆盖见 [证据入口](../../artifacts/product-acceptance/ui-6/README.md)。原实施批次未提交；本次 UI-6 实现与自检修复按用户要求共同提交，提交标识以 Git 历史为准。

## 验证与限制

最终 TypeScript、lint、build 均 exit 0；全量 Vitest 46 文件/353 项通过。命令、日志、截图、A6-01～A6-20 映射见证据入口。开始前定向基线 5 文件/50 项通过；迁移过程中完整前端首轮 46 文件/348 项通过，最终结果单独记录，不叠加计数。

首轮后端两个独立测试库同时迁移，因迁移操作共享 PostgreSQL 角色行，出现 `tuple concurrently updated`，19 项通过/13 个 setup error；改为顺序迁移后复验。补验 epoch 的初稿尝试修改终态 Run 被不可变事实约束拒绝，已改为新 Work 的真实 pause/resume 推进 epoch，保留原 v1 正向接受流程；最终后端主批 32 项通过，另有不重叠的 Work HTTP 验收版本冲突 1 项通过。首轮 Chromium 25 通过/1 失败，旧 Artifact 用例对“生成 HTML”的部分匹配同时命中“从回复生成 HTML”，改为精确按钮名，没有放宽业务断言。

跨模块 Chromium 26 项通过；Artifact/新增恢复场景补验 5 个不同场景通过（其中 3 项与主批重叠），源码恢复检查初始化一次、PUT 一次、保存两次、无 grant/accept。共 20 张截图。

七种视口与 reduced-motion 为 Chromium 自动化证据；布局通过桌面/移动截图阅读复核，长标题可换行，Composer 可滚动到达，背景由现有 native dialog 约束。未完成真实手机软键盘、人工 200% 文本缩放全流程、屏幕阅读器/对比度认证。A6-19/A6-20 应标记部分覆盖，不能称完整产品人工验收通过。Fake Executor 不证明真实模型、Temporal 或生产渠道执行质量。

## 后续与回退

UI-7：清理仍由开发创建弹窗使用的 TestPages/ArtifactsPage、ArtifactPanel 适配壳，以及 WorkPanel 的 `openArtifact` 单向导航适配与旧 Artifact CSS。领域 store 已无 openArtifactId/openVersionId/selectVersion/closeArtifact，不保留第二份选择。

BE-A1（服务端并发/预期 parent）、BE-A2（手工版替换原交付）、BE-AW1（一等 HTML 保存与持久 provenance）保持独立，未伪装为已交付。本轮源身份只存在客户端操作记录，不添加后台不存在的来源字段。

回退按前端文件与接线为单位；不删除服务器成果/Work/用户文件，不修改原 Task 状态，不需要数据库迁移。

## 自检修复补验

A6-03/A6-04/A6-09/A6-10 已补无指定版本重开、旧轮询/列表/消息响应乱序、同号终态、合法失败重试、手选优先、关闭/账户切换与 A→B→A；A6-01/A6-08 补仅消息行恢复及最多四个轮询请求。独立结果见 [修复证据](../../artifacts/product-acceptance/ui-6/self-review/README.md)，不复用原实施计数。默认初始化绑定本次成功加载，查询失败保留缓存预览，重试后选择本次最新成功版。

修复批次最终：TypeScript/lint/build 全部 exit 0；定向 12 文件/125 项、全量 Vitest 48 文件/373 项、Chromium 9 spec/30 项均通过。浏览器首轮 30 场景通过但包装器返回 143，随后直接 CLI 独立复验 exit 0；两轮不相加。详细命令与原失败记录均保留。
