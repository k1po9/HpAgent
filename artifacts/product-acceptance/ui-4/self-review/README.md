# UI-4 自检证据

日期：2026-10-07。对应 [自检记录](../../../../docs/implementation/ui-4-self-review.md)。基线 HEAD `40a318a5600a3123f3149e2788ddafaf28d59e9c`，实际审查其上的未提交 UI-4 实现。

- [已有回归日志](existing-tests.log)：9 文件、78 项通过，exit 0，38.86 秒。
- [定向反例日志](repro.log)：4 项测试失败，exit 1，3.59 秒；分别证实共享查询竞争、版本命令 key 丢失、撤权后读回失败跳过缓存失效、正常面包屑误报。
- [反例源码](UI4SelfReview.repro.test.tsx.txt)：保留预期正确行为断言；审查快照的代码下应失败。已从正式测试目录移出，没有修改业务实现或既有测试。
- [被审查源文件 SHA-256](source-sha256.txt)：记录本次前端工作区实现快照，供后续修复对照。

修复前反例的历史复验方法：先确认目标临时文件不存在，再将反例源码复制到 `web/src/components/workspace/UI4SelfReview.repro.test.tsx`，在 `web/` 执行：

```bash
npx vitest run src/components/workspace/UI4SelfReview.repro.test.tsx --maxWorkers=1 --testTimeout=15000 --reporter=verbose
```

反例只 mock 前端 API，不启动数据库、浏览器服务或外部模型；缓存内容为合成标记。执行后移除自己复制的临时文件，勿覆盖已有同名文件。修复时应将场景整合到正式测试，并补必要的真实 API 验证，不能仅改变预期使缺陷测试变绿。

## 修复证据（2026-10-07）

原 `.txt` 反例及失败日志对应修复前 SHA，作为历史保留。当前正式回归为 `web/src/components/workspace/UI4SelfReview.test.tsx`，14项含并发 Work GrantEditor/StrictMode、替换刷新、版本命令跨视图恢复/冲突/账户 reset、已确认撤权的部分成功及读回失败/跨视图/跨账户/同账户投影刷新/迟到 Model Input、合法及被删除目录/草稿保护。R2 第二次操作现在使用恢复入口“继续原版本提交”，仍断言全部原参数及 key 相同。

- [第一次修复定向结果](fix-focused.log)：81通过、1失败（原卸载后不刷新 activeRun 断言）。拆分全局缓存失效与 owner 视图刷新后保留该断言并通过。
- [定向修复结果](fix-focused-final.log)：10文件、91项通过，65.44秒；之后将双 Editor 场景明确为 Work，并在最终完整单测中复验。
- [TypeScript](fix-typecheck.log)、[ESLint/Prettier](fix-lint.log)、[构建](fix-build.log)：通过；lint无警告，保留构建 chunk 大小提示。
- [完整单测](fix-unit.log)：36文件、258项通过，91.68秒。
- [真实后端定向契约](fix-backend.log)：6项通过、1项 Starlette/httpx 弃用提示，84.42秒。P3 API 增补 same key 重放后 current revision=2 且 revisions数量=2，区别于只检查响应JSON相同；P2 Conversation/Work权限、P3持久化CAS同时复验。
- [浏览器矩阵](fix-e2e.log)：原计划11个spec、workers=1、真实API＋PG＋Redis＋Fake Executor，首轮使用新库 `hpagent_ui4_fix_e2e_20261007`，最终使用全新库 `hpagent_ui4_fix_e2e_final_20261007`；37项通过，5.6分钟。之后补同账户认证投影刷新分支，再跑完整前端门禁和账户浏览器相关用例，6项通过（1.0分钟），见 [fix-account-e2e.log](fix-account-e2e.log)；重复用例不累加到37项。
- [修复后源码 SHA-256](fix-source-sha256.txt)：包含本次正式代码/测试，与原 source-sha256.txt 分开。

后端测试库 `hpagent_ui4_fix_test_20261007` 与浏览器库各自隔离；migration/API/worker 三角色同库。浏览器Redis DB13、API8185/Vite5278、文件目录 `/tmp/hpagent-ui4-fix-files-20261007`。凭据仅临时0600配置，不纳入证据；原实施30项后端结果没有当作本次重跑。没有后端领域/迁移/新接口变更。Temporal、真实模型/渠道、人工辅助技术依旧未覆盖。

[首轮修复浏览器日志](fix-e2e-initial.log)：36通过、1失败。R4归根提示实际已显示，提示段落含“知道了”按钮使 `getByText(exact: true)` 无法匹配整段。改为 status 区域 `hasText` 内容与可见性断言，不删除提示断言；在最终全新库再次运行全部37项。

当前修复验证使用正式测试 `npx vitest run src/components/workspace/UI4SelfReview.test.tsx --maxWorkers=1 --testTimeout=15000`。历史 `.txt` 原封保留，R2旧脚本的再次选择提交流程不适用于新增的恢复入口；正式回归仍检查原 payload 和 key 全部相同。
