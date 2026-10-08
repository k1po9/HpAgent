# UI-6 HTML Artifact 验收证据

> 后续修复：自检 R1/R2/R3 已修复，原 4 个失败反例整理为正式回归并通过。新的定向/完整前端/Chromium 批次见 [独立修复证据](self-review/README.md) 与 [修复记录](../../../docs/implementation/ui-6-self-review.md)。以下命令表仍为原实施批次结果。

日期：2026-10-08（Asia/Shanghai）。基线 HEAD `9560c99675d6ebfd159909ea9d812790572c6a1d`，初始 dirty 为文档索引和未跟踪 UI-6 计划。实现与范围见 [报告](../../../docs/implementation/ui-6-html-artifact-report.md)、[计划](../../../docs/implementation/ui-6-html-artifact-plan.md)。本轮只改前端接线与测试；没有服务器协议/持久化模型/SSE 改动。

## 命令与结果

| 层 | 命令/范围 | 证据 |
| --- | --- | --- |
| 前序定向基线 | Artifact/Shell/Preview、Task/UI4 SelfReview | 5 文件/50 项通过：[baseline.log](logs/baseline.log) |
| 迁移定向 | Artifact 查询/命令、Shell、Preview、App、组件/Workspace/Thread | 9 文件/69 项通过：[focused-verified.log](logs/focused-verified.log)；最新新增卸载保护用例纳入最终全量，不同批次不相加 |
| 前端检查 | `npm run typecheck`、`npm run lint`、`npm run build` | [typecheck-verified.log](logs/typecheck-verified.log)、[lint-verified.log](logs/lint-verified.log)、[build-verified.log](logs/build-verified.log)，退出码见 [frontend-exits.txt](logs/frontend-exits.txt) |
| 全量前端 | `npm test -- --maxWorkers=2` | 46 文件/353 项通过：[unit-verified.log](logs/unit-verified.log)；限制 worker 仅降低本机 CPU 压力，不跳用例 |
| Chromium 跨模块 | 8 spec、workers=1，见下方 | 26 项通过：[e2e-final.log](logs/e2e-final.log) |
| Chromium 源码恢复补验 | Artifact + UI-6；增加下载字节、源码 PUT/409 恢复、loading/error/关闭 | [e2e-recovery.log](logs/e2e-recovery.log) 4 通过/1 文案断言失败，源码恢复定向最终 [e2e-source-recovery-final.log](logs/e2e-source-recovery-final.log) 1 通过；两批合计 5 个不同场景通过，与 26 项批次有 3 项重叠，不直接相加 |
| 真实后端 | Artifact generator/admission、Work integration、Workspace v4.1 P1 | 32 项通过：[backend-final.log](logs/backend-final.log) |
| 真实 HTTP 验收版本冲突 | Work foundation，accept-result 使用过期 If-Match 返回 409、无 acceptance 写入 | 1 项通过：[backend-acceptance-http.log](logs/backend-acceptance-http.log) |

```bash
cd web
npm run typecheck
npm run lint
npm run build
npm test -- --maxWorkers=2
npm run test:e2e -- e2e/artifact.spec.ts e2e/ui-6-artifact.spec.ts e2e/ui-5-tasks.spec.ts e2e/ui-5-recovery.spec.ts e2e/ui-4-workspace.spec.ts e2e/auth.spec.ts e2e/multi-tab.spec.ts e2e/a11y.spec.ts

# 回到仓库根目录，三个角色 DSN 指向专用测试库，不能沿用业务库默认值。
PYTHONPATH=src .venv/bin/python -m pytest test/test_web_artifacts.py test/web_persistence/test_web_artifacts.py test/web_persistence/test_work_integration.py test/web_api/test_workspace_v41_p1.py -q
PYTHONPATH=src .venv/bin/python -m pytest test/web_api/test_work_foundation.py -q
```

隔离库：`hpagent_ui6_contract_20261008`、`hpagent_ui6_e2e_20261008`（初轮）、`hpagent_ui6_e2e_final_20261008`（最终及新增恢复场景）；迁移/API/worker 三真实角色使用同一测试库。Redis DB12、API8186/Vite5279，文件目录 `/tmp/hpagent-ui6-files-20261008`。凭据由本机既有容器配置读取，未写入证据。Playwright trace 留在忽略的 test-results，不收录测试登录凭据/原始网络体。没有清理业务库或停止既有服务。所有最终命令与测试服务清理均 exit 0，见 [其他命令退出码](logs/other-exits.txt)。

交付文件清单见 [changed-files.txt](logs/changed-files.txt)，最终工作区状态见 [working-tree.txt](logs/working-tree.txt)，实现文件哈希见 [source-sha256.txt](logs/source-sha256.txt)。文档索引与计划是初始已有改动；其余为本次实现、测试与证据。

## A6 验收映射

“通过”表示列明的自动化层通过；真实模型、生产 Temporal、渠道和人工认证不在这些计数中。

| ID | 自动化结果及边界 | 主要证据 |
| --- | --- | --- |
| A6-01 | 显式生成、列出多对象；仅消息行恢复可自行从 running 收敛；空/未完成/非 assistant 无创建入口 | Thread、ArtifactMessageItems；UI6SelfReview 和修复浏览器回归 |
| A6-02 | 无 Conversation 的 Task 精确版本和返回上下文 | ArtifactInspector tests；UI-6 合成 Work E2E |
| A6-03 | 默认选择等待本次成功加载，缓存不固化旧版；数值版号、缺失版不替换、details 刷新 | UI6InspectorSelfReview；修复重开/失败恢复 E2E |
| A6-04 | v2 完成仍选 v1；同版本摘要接受终态；逐版合并阻止旧 running 覆盖完成 | UI6SelfReview（R1/R1b/R2）；真实丢响应与修复浏览器回归 |
| A6-05 | 历史版修改提示最新成功基准；基准变化预检拒绝；请求无 parent，服务端实际 parent=v1 | store/composer；真实 Work integration。跨标签无服务端并发锁，仍属 BE-A1 |
| A6-06 | Unicode 4000 边界、IME、失败/等待时编辑不丢稿；重放保留原 draft revision | store/Inspector/Thread tests |
| A6-07 | 同对象锁、创建/修改响应丢失同 key，实际重放不重复 | store；真实 API + Work integration |
| A6-08 | 消息与 Inspector 共用逐版去重轮询，最多四个 GET，终态停止；关闭不 cancel | UI6SelfReview、store、App 全量回归 |
| A6-09 | 网络不改 build status，恢复自行收敛；同号 failed 和合法 failed→running；403 清缓存停 poll | UI6SelfReview；无版本查询失败保留预览与重试 E2E |
| A6-10 | 逐版请求序号、对象 token 与账户世代；手选/关闭/A→B→A/退出阻止迟到默认选择；旧账户队列不阻塞新账户 | 两个 UI6SelfReview、App session、multi-tab 完整回归 |
| A6-11 | scripts-only iframe、来源验证、旧 frame 无效、切 HTML 清错 | Preview tests；真实浏览器交互 |
| A6-12 | 所选 HTML 字节/标题-vN/MIME；定时/关闭/退出 URL 清理 | download tests；Artifact 下载流字节 E2E |
| A6-13 | text/plain、默认 html.txt、v1 来源、无 grant/accept、node 回链 | WorkspaceCommands；源码保存 E2E |
| A6-14 | 丢 PUT 后 ready metadata；不重传，保存恢复复用 file_id/key | UI4SelfReview/WorkspaceCommands；真实 PUT 恢复 E2E |
| A6-15 | 合成 409 明确改目标复用 ready 内容；切源旧完成不关闭新窗 | WorkspaceCommands/UI4；源码恢复 E2E |
| A6-16 | 既有 file_id 直接保存、未创建上传，不改现有输出入口 | 原 Workspace/Task tests、UI-4 E2E |
| A6-17 | 原 Work v1 不变，手工 v2 新 Work/Run；Inspector 无接受最新版入口 | 真实 Work integration；Task 原引用 E2E |
| A6-18 | role/revision/epoch/evidence 资格；错误 v2/旧 revision/旧 epoch 拒绝；过期 If-Match HTTP409 无写入 | taskPresentation；真实后端与 Work HTTP |
| A6-19 | 部分覆盖：Tabs 键盘、dialog/Esc 分层、焦点返回、长标题、七尺寸无页面溢出 | Chromium/screenshots；未做完整人工键盘与 200% 文本流程、读屏/对比度认证 |
| A6-20 | 部分覆盖：390/360 前景 Composer、背景 native modal inert、reduced-motion | Chromium/screenshots；真实手机软键盘未覆盖 |

## 截图与流程

[screenshots](screenshots/) 共 20 张，包含 360×800、390×844、768×1024、1024×768、1280×800、1440×900、1920×1080，长标题、reduced-motion、真实版本历史、构建失败、缺失版本、403、空版本，以及补验 loading/query-error/关闭/移动 Composer/源码保存恢复。合成对象用于可重复异常与布局验证；真实成功图使用隔离测试账户与 Fake Executor，不是生产业务截图。

成功、失败恢复和迟到响应操作步骤见 [flows/README.md](flows/README.md)。

## 失败记录与覆盖限制

- [backend-initial.log](logs/backend-initial.log)：19 通过/13 setup error；两个测试库并行迁移更新同一 PostgreSQL 角色行，`tuple concurrently updated`。顺序复验解决，无服务器变更。
- [backend.log](logs/backend.log)：31 通过/1 失败；新增测试初稿修改 Run control epoch 被数据库不可变事实保护拒绝。已改用新 Work 的 pause/resume 命令推进 epoch；[backend-final.log](logs/backend-final.log) 32 通过。
- [e2e-initial.log](logs/e2e-initial.log)：25 通过/1 失败；旧按钮名称部分匹配引起 strict-mode 多元素，精确定位后完整 26 通过。
- [unit-final.log](logs/unit-final.log)：351 通过/1 失败；原 Thread 用例在拒绝响应尚未释放提交锁时点击第二次，已显式等待发送控件恢复可用，保持“拒绝保留/成功清空”断言；最终独立日志列于上表。
- [e2e-recovery.log](logs/e2e-recovery.log)：4 通过/1 失败；源码恢复已进入合成 409，完整错误文案带请求标识，原新用例精确文本断言遗漏后缀。修正为匹配真实错误消息，最终定向 1 通过，保留上传/PUT/save 次数和无 grant/accept 断言。
- 中途 lint 的 control-character regex/格式问题已在定点实现修正，最终状态以 verified 日志为准。Vite 大 chunk 警告仍是构建提示，代码分割/性能收尾属于 UI-7/UI-8。

自动化交付与人工产品验收分开：A6-19/A6-20 未覆盖项不能标为完整通过；未验证真实模型、Temporal、生产渠道。BE-A1/BE-A2/BE-AW1 未交付。保留旧开发页壳与无状态导航适配，UI-7 再清理。

## 自检修复最终批次

R1/R2/R3 已修复，原 4 反例由失败转为通过并扩展为 20 个正式场景。TypeScript/lint/build exit 0；定向 12 文件/125 项、全量 Vitest 48 文件/373 项、跨模块 Chromium 9 spec/30 项通过；计数互有覆盖，不累加。[修复日志、4 张新截图与源码哈希](self-review/README.md) 独立于本页原实施批次。原 20 张截图保留原副本。跨模块浏览器首轮包装器 143 与随后直接 CLI exit 0 的两轮记录均保留。
