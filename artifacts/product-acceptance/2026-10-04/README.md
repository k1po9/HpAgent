# 现有 Web 产品覆盖审计与浏览器验收

修复后最新状态见 [实施与复验结果](/home/hp/workspace/HpAgent_web/artifacts/product-acceptance/2026-10-04/repair-results.md)：F01/F02/F05/F06/F07/F08/F10 已修复并复验，F03 保留已通过状态，F09 镜像漂移已对齐；模型网络及生成成果仍阻断。以下内容保留初次审计记录，覆盖矩阵已补充修复后的证据与边界。

验收时间：2026-10-03 23:17 至 2026-10-04（Asia/Shanghai）。代码基线：`69b079af46f8ac069b55e08ec11595d28f0986c3`，包含原有未提交修复。本轮保留这些修复，只新增 Workspace 的三处 UUID 兼容调用替换及本验收报告；没有提交或推送。

**结论：具备部分真实后端浏览器验收条件，尚不具备完整真实产品 E2E 通过条件。**

- 前端已有账号、聊天、附件、长期 Workspace、部分授权/版本、Artifact、Work 控制与成果展示、Research 成果展示、Trace、QQ 绑定挑战入口。Work/Research 创建通过聊天中的 Main Agent；没有独立表单不等于没有产品入口。
- 实测可用：注册/登录/退出、对话创建和快速切换、消息刷新恢复、文件上传/下载/保存/改名/搜索/保留说明、静止资源授权与撤销、v1 历史升级、Work 暂停/恢复/停止、后台提醒履约、账号隔离与 CSRF 边界。
- 实测失败：给定 localhost Origin 与部署不一致；创建失败无 UI 提示；真实聊天模型超时；窄屏布局；Work SSE 返回 406；终态 Run 资源面板错误提示；Research 空状态无说明。另有已停止 Work 仍展示未来到期时间的文案问题。
- 已最小修复并复测：HTTP IP 地址下 Workspace 使用 `crypto.randomUUID()` 导致上传在请求前失败。改用已有 `newIdempotencyKey()` 后真实浏览器上传、保存、下载通过。
- 未验证/阻断：完整聊天成果、计划执行成果、运行中的模型/工具取消、已读取输入撤权后的真实停止、v2/CAS、Artifact 构建/编辑、Generic Work 最终成果、Research 证据/报告/保存、文档生成与重规范化、长期记忆保留/跨对话召回、QQ 绑定完成。不能把已有方法、接口、列表或请求接受算作这些能力通过。

交付文件：

- [小白人工验收教程](/home/hp/workspace/HpAgent_web/artifacts/product-acceptance/2026-10-04/manual.md)
- [产品覆盖矩阵](/home/hp/workspace/HpAgent_web/artifacts/product-acceptance/2026-10-04/coverage.md)
- [失败复现、分类和最小建议](/home/hp/workspace/HpAgent_web/artifacts/product-acceptance/2026-10-04/failures.md)
- [环境、证据和验证边界](/home/hp/workspace/HpAgent_web/artifacts/product-acceptance/2026-10-04/evidence.md)
- [修复检视与实施顺序](/home/hp/workspace/HpAgent_web/artifacts/product-acceptance/2026-10-04/repair-review.md)

本轮操作了真实 headless Chromium，查看了实际截图、页面文本和网络请求，没有启用 Fake Executor 或拦截伪造业务响应。Windows computer-use 因 `sandboxCwd is not a local file URI` 初始化失败；本轮不是人类亲自执行的桌面验收，人工签收仍需按教程操作。

真实模型快照记录 MiniMax-M3：记忆查询改写成功，聊天决策三次超时并最终 `model_unavailable`。模型账户记账为 24,770 tokens，其中 24,444 是结果未知时的估算，不能说成 Provider 确认消费。只发送了一次该完成场景；额外附件场景在 queued 时取消，没有模型请求。

Work 的成功案例是通过验收 API 创建的账户收件箱提醒：真实 Temporal Run `succeeded`，Work `completed`，持久化完成凭据引用投递回执，页面显示已存入收件箱。它不需要模型，不能替代 Main Agent 自然语言创建、Generic Work 或 Research 的模型成果验收。

已有 17/17 浏览器测试使用 Fake Executor。历史非 PostgreSQL 单元测试仍有 36 项失败，本轮未重跑或关闭该积压。**不能宣称全量 CI 已通过。**
