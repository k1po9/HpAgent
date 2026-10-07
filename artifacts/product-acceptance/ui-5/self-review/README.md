# UI-5 自检修复证据（2026-10-08）

对应 [自检与修复记录](../../../../docs/implementation/ui-5-self-review.md)。修复前 HEAD `be57e0bd22623974ce57498aed78c086fc5f6673`，UI-5 原实现仍在工作区；本批按用户要求修复 R1–R3 并一并提交 UI-5 实现。原实施日志、截图和 316/28/37 结果保留，不当作本批复验结果。

## 结果

| 验证 | 本批结果 | 证据 |
| --- | --- | --- |
| 原自检定向 | 9 文件、80 项通过；28.88s | [regression.log](regression.log) |
| 原自检反例 | 3 项失败；12.65s | [repro.log](repro.log)、[原反例源码](UI5SelfReview.repro.test.tsx.txt) |
| 本批修复前重复反例 | 3 项失败；8.41s，exit 1 | [before-fixes.log](fix-logs/before-fixes.log) |
| 修复后定向 | 3 文件、26 项通过；20.61s，exit 0 | [targeted-initial.log](fix-logs/targeted-initial.log) |
| TypeScript / lint / build | 全部通过，exit 0；构建保留既有 >500 kB chunk 提示 | [typecheck.log](fix-logs/typecheck.log)、[lint.log](fix-logs/lint.log)、[build.log](fix-logs/build.log) |
| 完整 Vitest | 43 文件、329 项通过；189.74s，exit 0 | [unit-full.log](fix-logs/unit-full.log) |
| Chromium | 五个 spec、15 项通过；4.9min，exit 0 | [e2e.log](fix-logs/e2e.log) |

第一次 typecheck 检出测试延迟 Promise 的泛型推断为 never，补成 `Promise<unknown>` 后复跑，保留 [typecheck-initial.log](fix-logs/typecheck-initial.log)。该问题在测试夹具，不改运行逻辑。

## 覆盖与轨迹

正式 [TaskSelfReview.test.tsx](../../../../web/src/components/tasks/TaskSelfReview.test.tsx) 包含 13 项：R1 五项、R2 三项、R3 五项。原 R1 的再次点击步骤改为先确认仍展开再收起/重开，原两次请求和文件可见断言保留，并分别覆盖展开与收起期间的终态变化。13 项为本批正式回归数，不与旧反例数量累计。

- R1：运行中读到空文件列表，Work 更新、同 Run 成功后自动再读且保持展开；收起后终态再打开也拿到新文件。报告与文件独立失效/刷新，空结果可以主动刷新。旧运行状态请求晚到不能覆盖终态，新账户不回灌旧输出。
- R2：undefined/null continuation，通过成果页 URL 或页签进入均不崩溃，显示只读提示和刷新；验收、投递变更不可达，资料管理及目标变更降级；取回有效快照后重新开放合法动作。
- R3：缓存要求的刷新失败后编辑目标、约束、计划时间和原因，重试加载中/失败/成功均保留输入。新 revision 单独呈现，审阅后以新 If-Match 提交；403/404 清理，切对象/账户丢弃旧草稿，旧读取晚到不能替换新账户要求；关闭仍要求放弃确认。已有 409/未知响应幂等恢复测试包含在完整单测。

浏览器新 `ui-5-recovery.spec.ts` 有三项：前两项是合成网络快照，证明缓存/异常 UI；第三项在真实 API 创建未来提醒，读取故障由 route.abort 注入，成功重试后真实 POST 修订，再 GET 读回目标、约束和 Shanghai due_at。没有以合成 Run 快照证明真实任务/Temporal 收敛。

## 环境与边界

测试 PostgreSQL：全新可丢弃 `hpagent_ui5_fix_e2e_20261008`；API8185/Vite5278，Redis DB11，文件目录 `/tmp/hpagent-ui5-fix-files-20261008`，三个数据库角色同库。凭据取本机现有服务配置，不保存到证据。npm 子进程在独立 process session，避免此前测试封装清理影响父进程；最终实际退出码另记录。

未清理业务库、未停止现有业务服务、未安装依赖。未修改后端源码、API 契约或迁移，因此本批没有重复后端 37 项；历史后端记录仍保留。真实模型、QQ、Temporal、人工读屏和持续压力未覆盖。

本批任务截图单独输出到 [screenshots/](screenshots/)，不覆盖原 UI-5 实施截图。Artifact 回归产生的旧阶段截图在复验结束后复制到本目录的 `regressions/`，再恢复历史原件。变更清单见 [changed-files.txt](changed-files.txt)。


## 截图与退出码

- [终态输出刷新](screenshots/terminal-output-refresh.png)
- [异常快照成果页](screenshots/invalid-output-readonly.png)
- [要求重试后的完整草稿](screenshots/requirement-draft-retry.png)
- [移动任务详情](screenshots/task-inspector-390x844.png)、[200%文本](screenshots/task-text-200-percent.png)
- [本批退出码](fix-logs/command-exits.txt)

本批浏览器总数包含三项新恢复场景、五项既有 UI-5、四项 manual-repair、一项 Artifact 和两项 a11y。三项新场景中两项为网络快照夹具、一项真实 API 修订，性质分别记录在测试名；没有宣称重新运行原八个 spec 的完整浏览器矩阵或真实后端 37 项。
