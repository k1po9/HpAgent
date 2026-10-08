# UI-6 独立自检与修复证据

2026-10-08（Asia/Shanghai）。当前结论：R1/R2/R3 三个 P2 已修复，原 4 个反例归入正式测试并通过。下面保留原自检批次证据，并独立列出修复复验。详细根因与修复门槛见 [自检记录](../../../../docs/implementation/ui-6-self-review.md)。

- [commands.txt](commands.txt)：本次实际命令、退出码与范围。
- [existing-tests.log](existing-tests.log)：10 文件、105 项既有定向回归通过。
- [repro.log](repro.log)：R1/R1b/R2 三个 store 反例失败。
- [inspector-repro.log](inspector-repro.log)：R3 默认选择反例失败。
- [Store 复现代码](UI6SelfReview.repro.test.ts.txt)：运行时放置于 `web/src/store/UI6SelfReview.repro.test.ts`。
- [Inspector 复现代码](UI6InspectorSelfReview.repro.test.tsx.txt)：运行时放置于 `web/src/components/artifact/UI6InspectorSelfReview.repro.test.tsx`。

重现时将 `.txt` 文件复制回对应路径，按 commands.txt 执行两条反例命令。断言描述期望的正确行为，原自检批次 exit 1 是缺陷复现结果。当前已整理为 `UI6SelfReview.test.ts` 和 `UI6InspectorSelfReview.test.tsx`；不要同时复制 `.txt` 导致重复收集。

原自检时核对原证据清单中的 35 个文件哈希一致；该批次未重跑数据库、Chromium、真实模型或 Temporal，不叠加原实施计数。修复后的独立源码哈希另列于 fix 目录。


## 修复批次 · 2026-10-08

| 检查 | 本批结果与证据 |
| --- | --- |
| 修复前重现 | 2 文件、4 失败，exit 1：[before.log](fix/before.log) |
| 正式反例与边界 | 2 文件、20 项纳入定向与全量；原 R1/R1b/R2/R3 全部通过 |
| 相关模块定向 | Artifact/Shell/消息/Preview/Workspace/Task，12 文件、125 项通过，exit 0：[focused-final.log](fix/focused-final.log) |
| 完整前端检查 | TypeScript/lint/build 均 exit 0，Vitest 48 文件、373 项通过：[退出码](fix/frontend-exits.txt) 与 [完整单测日志](fix/unit-verified.log) |
| Chromium 新场景定向 | 2 通过，exit 0：[e2e-focused.log](fix/e2e-focused.log) |
| Chromium 跨模块 | 9 spec、30 项通过，exit 0；Artifact/UI-6/UI-5/Workspace/Auth/Multi-tab/A11y：[直接执行日志](fix/e2e-regression-verified.log) |
| 交付快照 | [命令与退出码](fix/commands.txt)、[源码哈希](fix/source-sha256.txt)、[提交文件清单](fix/changed-files.txt) |

[正式 Store 回归](../../../../web/src/store/UI6SelfReview.test.ts)、[正式 Inspector 回归](../../../../web/src/components/artifact/UI6InspectorSelfReview.test.tsx) 验证逐版本乱序保护、同号 completed/failed、较新 failed→running 合法重试、更高版本保护、旧列表保留 POST 新版、终态停止 poll、仅消息行断网恢复与四并发上限。Inspector 覆盖冷加载、显式历史深链、加载中手选、缓存保留/失败重试、关闭迟到、账户切换、A→B→A。

[浏览器场景](../../../../web/e2e/ui-6-self-review.spec.ts) 使用真实 PostgreSQL/Redis/API + Fake Executor 创建 HTML，随后注入版本 GET 的延迟/503 和逐版 GET 的网络失败来验证两条流程。合成 v8/v9 用于可重复的加载顺序，不声称由后端真实生成。[4 张新截图](fix/screenshots/) 分别展示新默认 v8、失败时缓存预览、重试默认 v9、恢复消息行完成。已有 UI-6 E2E 的真实版本修改、丢响应幂等、源码保存和 Task 引用隔离也纳入跨模块复验。

专用库 `hpagent_ui6_review_20261008`，Redis DB12，API8186/Vite5279，测试服务退出后清理；未清理业务库或停止原容器。原 Python 契约日志保留历史，修复批次未重跑 Python 测试。真实模型/Temporal、生产渠道、真实手机软键盘及完整人工 200% 文本/读屏认证未覆盖；A6-19/A6-20 仍为部分覆盖。

复跑脚本副本：[run-frontend.py](fix/run-frontend.py)、[run-e2e.py](fix/run-e2e.py)。后者需要本机既有测试容器配置；`UI6_DATABASE` 必须指向隔离测试库。

包装器退出说明：[e2e-regression.log](fix/e2e-regression.log) 中 30 个场景均通过，但外层返回 143，未记为命令 exit 0；随后使用 [直接 CLI 脚本](fix/run-e2e-direct.py) 独立验证，结果以 verified 日志及 commands.txt 的最终退出码为准。

提交前仅规范化日志的行尾空白与末尾空行；测试输出、失败信息、结果计数和时间保留，未删改断言或覆盖失败批次。
