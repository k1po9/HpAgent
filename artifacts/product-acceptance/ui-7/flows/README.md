# UI-7 本轮操作记录

## 成功（真实 API）

登录 alice → 新建对话 → 完成测试执行器回复 → AI Header「从回复生成 HTML」→ 选择已完成回复 → 填入“用中文生成报告” → 创建。
断言真实 POST body 为 `{"instruction":"用中文生成报告"}`，HTML Inspector 自动打开，轮询后出现 sandbox 预览。关闭回到 AI；既有 Artifact E2E 继续覆盖创建/修改丢响应的同 key 恢复、下载与源码另存。
证据：e2e-regression-verified.log 的正式 Header 用例、UI-6 真实 API 用例；对应本轮截图在 regression-evidence/ui-6。

## 失败恢复（真实 API / 明确的网络注入）

输入合法但不存在的 Run UUID → GET 返回 404 → 就地“对象不可用”，执行编号保留、未伪造 Work 的 assistant_message、未打开任意缓存对象。
上传保存与版本生成的网络注入沿用真实 API：丢 PUT、姓名/版本冲突、授权拒绝后补授权；已 ready 内容不重新上传；UI-4/5/6 恢复 E2E 断言保留。
证据：lookup-unavailable.png、e2e-regression-verified.log、regression-evidence/ui-4 和 ui-6。

## 并发与迟到（Vitest 延迟 Promise）

HTML 创建期间切来源、关闭弹窗或换屏；命令可以正确归入原领域对象，组件导航 token/存活检查拒绝自动抢回面板。生成要求的在途编辑保留。
Run 查询 A→B→A、同 ID 重查和退出：只接受最新 token、同账户、同导航世代的已核实响应；拒绝旧成功；403/404 保留纠正输入。
候选分页重复点击只发一次；queued→running 重新查询；取消/切对象后旧页和失败不回灌。
证据：ArtifactCreateForm.test.tsx、RunLookup.test.tsx、RunResources.test.tsx、WorkspaceMigration.test.tsx；unit-final.log。

## Host 生命周期与前景层（Chromium + 组件计数）

同一 HTML Inspector 在七视口及 1279↔1280 / 959↔960 / 599↔600 来回 resize：文本草稿与 iframe 内按钮状态保持，Artifact 列表读取初始/最终均为一次。
移动打开源码保存：原 Inspector hidden/inert、仅保存 dialog 活跃；Escape 恢复父层和保存触发按钮；第二次 Escape 关闭 Inspector 并返回 Canvas 标题。
桌面打开任务收件箱后切手机：原任务 DOM 哨兵保持。嵌套要求编辑的放弃确认逐层 Escape，未提交输入和原任务上下文保持。
证据：geometry.json、Surface.test.tsx（mount/unmount）、mobile-save-layer.png、mobile-inbox-layer.png、UI-7 浏览器用例。

这是本轮自动化操作记录。真机软键盘、完整读屏、真实模型与渠道不由上述结果推导。
