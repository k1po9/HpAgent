# UI-4 自检记录

> 修复状态（2026-10-07）：R1～R4 已修复。原审查与失败证据保留在下文；修复及最终复验见文末。

日期：2026-10-07（Asia/Shanghai）。审查基线 HEAD：`40a318a5600a3123f3149e2788ddafaf28d59e9c`，审查对象为其上尚未提交的 UI-4 工作区实现。依据 [实施计划](ui-4-workspace-permissions-plan.md)、[实施报告](ui-4-workspace-permissions-report.md)及[验收证据](../../artifacts/product-acceptance/ui-4/README.md)。实际读取文件的 SHA-256 见[快照清单](../../artifacts/product-acceptance/ui-4/self-review/source-sha256.txt)，用于区分后续未提交改动。

**结论：确认 4 个 P2 实现问题，均已通过定向反例复现。UI-4 不能据原有通过记录认定以下边界已完整通过；修复后需要补回归。** 原日志中的 244 项单测、37 项 Chromium、30 项后端契约结果保留为历史证据，不改写或累加。本轮已有相关回归 78/78 通过；新增的 4 项预期行为断言均失败，失败是本次缺陷证据。

本轮仅执行静态审查与前端定向测试，未修改业务实现、未修改现有正式测试断言、未启动数据库/API/浏览器环境。临时反例已移入证据目录为 `.txt`，不会污染默认 Vitest/TypeScript 测试集合。

## R1 · P2：共享查询遇到并发订阅者时主动废弃第一个视图

位置：[useWorkspaceQuery.ts](../../web/src/components/workspace/useWorkspaceQuery.ts)，第 19–32 行；[workspace.ts](../../web/src/store/workspace.ts) 的 `query`、`owners` 和 `flights`。

触发：两个仍挂载的消费者同时读取同一个 key，例如任务页的 GrantEditor 与 File Inspector 对同一个 Work 读取授权。第一个请求尚未完成时，第二个 hook 发现 `cache[key].loading` 为 true，反而以 `force=true` 调用 query。

结果：第二个请求绕过 flights 去重并覆盖 owner token。第一个请求即使成功，也会被 controller 判为 Stale query；它的消费者把该异常写成错误。第二个请求成功后，第一消费者仅依赖自己的 result，不订阅共享 cache 成功值，因此不会自动恢复。用户会看到同一份授权在一个视图正常、另一个视图查询失败。

复现 R1：两个 hook 同时加载同 key，共用受控 Promise。预期只请求一次且两处显示 `rules`；实际请求 **2 次**，第一处显示“查询失败”，第二处正常。测试不是只检查请求数，还确认失败状态遗留。

修复建议：普通消费者加入已有 flight；只有明确刷新才开启新请求世代。共享查询的替换/失效不应把正常订阅者永久置为错误。A→B→A 的视图生命周期与共享 query generation 分别管理，不用“已有请求正在加载”推导强制刷新。

回归门槛：两个真实 GrantEditor 共用主体、StrictMode、刷新与并发订阅、A→B→A、账户 reset；所有存活消费者收敛到最新有效结果，无额外请求或永久假错误。对应 U4-A12/U4-A17 与共享查询去重声明。

## R2 · P2：版本提交意图在切换页签后丢失，未知结果无法原样重放

位置：[FileVersions.tsx](../../web/src/components/workspace/FileVersions.tsx)，第 23 行及第 122–161 行；[FileInspector.tsx](../../web/src/components/workspace/FileInspector.tsx) 条件挂载 `FileVersions`。

触发：用户提交新版本，服务器可能已落地，但响应丢失；用户切到其他页签再返回，选择同一 Run/输出后重试。版本命令的 payload/key 只存在 FileVersions 的局部 ref 中，切页签或关闭 Inspector 会卸载它；当前代码也未阻止在请求处理中切页签。

结果：恢复相同输入时生成新 operation key。若第一次已成功，旧 revision 加新 key 会得到 CAS 冲突，而原 key 本可返回已完成结果；之后按“重新确认”或“另存”操作可能造成无必要的新版本/入口。后端 CAS 仍有效，本轮未证明覆盖他人版本或破坏后端数据；问题是前端丢失未知命令的恢复身份。

复现 R2：第一次更新返回 `response lost`，卸载/重新挂载版本视图并提交相同 node/run/file/revision/sha。两次调用前五个参数完全相同，但 operation key **不同**，原意图重放断言失败。测试在客户端层复现，没有声称本轮在数据库中创建了重复版本。

修复建议：与上传/保存相同，把版本命令放入按 account generation＋node 归属的操作状态中，保留 payload、expected_revision、expected_sha256、key 和未知结果状态。关闭/切页仅改变视图；重开可恢复原命令。只有明确拒绝后修改输入、或用户明确发起新版本时生成新 key。

回归门槛：成功落地但丢响应、尚在执行时切页签/关面板、重开原样恢复、CAS 明确拒绝后新意图、退出清理；并在真实 API 验证同 key 不新增 revision。对应 U4-A11/U4-A17 和计划 §5.3。

## R3 · P2：Composer 撤权的后续读回失败会跳过已确认结果的全局失效

位置：[ConversationResources.tsx](../../web/src/components/conversation/ConversationResources.tsx)，第 240–247 行。

触发：Composer Chip 的 DELETE 已明确成功，返回 `affected_runs`；紧接着 `listConversationResources` 读回失败。代码先 await 读回，再做 `observeRevocation` 与 `invalidateQueries`，因此异常直接跳过这两步。读回成功但用户已切换视图时，`canWriteOwnerView` 提前 return 也会跳过同类全局处理。

结果：已确认受影响的 Run 不加入跟踪，Workspace 授权缓存不失效，旧 Model Input 缓存也未清理。权限列表可以继续显示“待确认”，但这不应抹掉已确认 DELETE 返回的事实。这里证明的是同账户已加载缓存与前端状态不同步，**不是**跨账户泄漏或绕过服务端 Model Input entitlement；后者仍由真实接口判定。

复现 R3：设置一份合成 Model Input 缓存；DELETE 明确返回 `affected: stopping`；后续授权 GET 报网络错误。实际旧缓存仍存在、`useAffectedRuns.states` 为空。预期清缓存并记录已知 affected Run 的两个断言均失败。原 ConversationResources 的“读回也失败”测试只检查操作恢复，没有覆盖这一新失效接线。

修复建议：同账户世代仍有效时，先消费成功 DELETE 的 affected_runs、失效指定主体及诊断缓存，再独立执行列表读回。全局账户级失效与当前视图 owner 分开；切视图不能丢掉原操作结果，切账户则不得更新新账户。保留剩余规则读回和部分失败重试语义。

回归门槛：DELETE 成功＋读回失败、部分规则成功＋读回失败、切视图后完成、切账户后完成、迟到 Model Input 请求；确认已知受影响 Run 进入跟踪且旧正文不回灌。对应 U4-A14/U4-A18 和报告中的撤权缓存失效声明。

## R4 · P2：正常点击面包屑也提示目录不存在

位置：[WorkspaceScreen.tsx](../../web/src/components/workspace/WorkspaceScreen.tsx)，第 64–67 行；真正的无效目录规范化在第 33–45 行。

触发：点击任一合法面包屑，包括当前根目录。点击处理无条件写入“目录不存在或不可访问，已返回根目录。”，即使所选祖先存在，甚至目标并非根目录。

结果：正常导航被错误解释为权限/对象异常；反而真正无效 dir 的自动归根分支没有设置该说明。

复现 R4：加载合法根节点后点击“根目录”，实际出现上述错误状态提示，预期不存在该提示的断言失败。

修复建议：将提示移到确认无效目录的归根分支，正常面包屑导航不产生异常说明；导航被未提交权限编辑拦截时不要提前声明“已返回”。

回归门槛：合法祖先/当前目录、无效 dir、真实已删除目录、权限草稿导航拦截；分别断言目标 URL 和提示内容。对应 U4-A01。

## 本轮验证与证据

### 已有相关回归

在 `web/` 执行：

```bash
npx vitest run src/store/workspace.test.ts src/components/workspace src/components/conversation/ConversationResources.test.tsx src/store/sessionIsolation.test.ts src/api/client.test.ts --maxWorkers=1 --testTimeout=15000
```

结果：**9 文件、78 项通过，exit 0，38.86 秒**。此批在创建临时反例前启动；不含新增反例。日志：[existing-tests.log](../../artifacts/product-acceptance/ui-4/self-review/existing-tests.log)。

### 定向反例

在 `web/` 执行：

```bash
npx vitest run src/components/workspace/UI4SelfReview.repro.test.tsx --maxWorkers=1 --testTimeout=15000 --reporter=verbose
```

结果：**1 文件、4 项失败，exit 1，3.59 秒**，无超时；多个 soft assertion 使日志展示 6 个断言失败，不是 6 个独立测试。测试源码已保存至 [UI4SelfReview.repro.test.tsx.txt](../../artifacts/product-acceptance/ui-4/self-review/UI4SelfReview.repro.test.tsx.txt)，日志：[repro.log](../../artifacts/product-acceptance/ui-4/self-review/repro.log)。

### 静态与证据核对

- 阅读新 Workspace 查询/操作/权限/版本/保存组件与相关 Shell、会话 reset、ApiClient 接线；核对 Work 条件命令与 Workspace CAS 的后端实现。
- 核对原最终日志尾部的 244 项单测、37 项 Chromium、30 项后端契约计数；它们不包含本轮反例，仍属于实施时的环境和代码证据。
- 抽查已有桌面空间及手机 Inspector 截图：手机长标题和 Markdown 正文可见；桌面截图处于列表滚动位置，不能单凭这一张证明搜索区和表头全貌。未把截图当作运行期状态正确性的证明。
- 本轮未重跑全量 244 项、typecheck/lint/build、37 项浏览器、后端契约或 Temporal；未将原报告中的这些结果写成本轮复测通过。
- `git diff --check` 通过。检查文档链接与证据文件存在；未创建 commit/PR。

建议按 R3→R2→R1→R4 修复，并把反例转为正式回归。修复后在最终代码上重跑受影响测试及原阶段交付门禁，分别记录真实 API/Fake Executor/Temporal 覆盖边界，再更新实施报告 U4-A 对应结论。

## 修复记录（2026-10-07）

原审查结论、SHA 清单和四项失败日志保留为修复前证据。正式回归现在位于 `web/src/components/workspace/UI4SelfReview.test.tsx`，保留四项预期行为断言，并补齐并发/迟到/恢复边界，合计14项。R2 的操作界面增加“继续原版本提交”，测试通过该入口恢复原意图，没有将“不丢 operation key”改成较弱的断言。

| 问题 | 修复 | 额外回归 |
| --- | --- | --- |
| R1 | 普通 hook 加入已有 flight，视图直接订阅同一 keyed cache。仅显式重试 force；旧 owner 的 AbortError 不写入存活消费者。查询中已有 flight 优先于旧缓存值 | 两个实际 Work GrantEditor 在 StrictMode 下仅请求一次；显式刷新替换在途请求后两者收敛；A→B→A 共用 flight；账户 reset 拒绝旧结果 |
| R2 | 增加账户 generation＋node 所属的 `versionOperations`，冻结 run/file/revision/sha/key。pending/unknown 锁定输入，卸载只改变视图，重开恢复。网络/5xx 结果未知仍用原 key；明确 CAS 拒绝后才允许重新确认新意图。session reset 同步清理 | 在途关闭重开；未知响应后当前 metadata 已变仍重放旧 CAS；明确冲突读回并再次确认才发新 key；退出后迟到成功不回填。真实 API 增补同 key 重放后 current=2 且历史恰2项 |
| R3 | 每条成功 DELETE 同账户世代即处理 affected Run、Workspace/Model Input/Run Inspector 失效，不等待后续 GET。权威读回与当前 owner UI 更新独立；跨视图保留已确认结果，跨账户拒绝旧结果。聊天刷新仍要求原视图 owner 存活 | DELETE 成功读回失败；部分成功另一 DELETE 未决时已清缓存；迟到 Model Input 不回灌；原视图卸载后成功；同账户认证投影刷新仍消费结果；切账户后成功不污染新账户；保留原部分失败/剩余规则重试断言 |
| R4 | 仅确认无效目录且实际接受归根导航后写提示，正常面包屑清旧提示。权限草稿先等待继续/放弃选择；确认时再核对最新树和 route | 合法当前目录；目录被新树删除；草稿阻止归根时 URL 和提示不提前变化；浏览器补合法根目录无异常提示和无效 dir 归根说明 |

定向修复验证使用原自检命令增加正式反例测试，共 **10 文件、91 项通过**，65.44秒，见 [fix-focused-final.log](../../artifacts/product-acceptance/ui-4/self-review/fix-focused-final.log)。第一次修复定向验证仍有1项原回归失败（卸载后不应刷新 activeRun），见 [fix-focused.log](../../artifacts/product-acceptance/ui-4/self-review/fix-focused.log)；已拆开账户级失效与 owner 视图刷新，未删改该断言。

真实后端定向契约：`test/web_api/test_workspace_v41_p3.py`、`test/web_api/test_workspace_v41_p2.py`、`test/web_persistence/test_workspace_v41_p3.py`，**6项通过、1项 Starlette/httpx 弃用提示**，84.42秒，见 [fix-backend.log](../../artifacts/product-acceptance/ui-4/self-review/fix-backend.log)。使用新可丢弃库 `hpagent_ui4_fix_test_20261007`，migration/API/worker 三角色同库；没有后端实现、迁移或新接口修改，仅加强版本重放历史数量断言。原30项后端实施记录不作为本次重新运行的数量。

最终静态门禁、完整单测、Chromium 矩阵和修复后 SHA 清单见下方最终验证及证据目录。首轮修复浏览器36通过、1失败：实际归根提示存在，但段落包含关闭按钮，精确整段文本 locator 失配；改为 status 区域的提示内容和可见性断言后，使用全新可丢弃库复跑完整矩阵。真实 Temporal、真实模型/渠道和人工辅助技术未覆盖。

### 最终验证

- `npm run typecheck`、`npm run lint`、`npm run build`：通过，lint无警告；构建仍有既有chunk大小提示。
- `npx vitest run --maxWorkers=1 --testTimeout=15000`：**36文件、258项通过，91.68秒**。正式修复反例及扩展现为14项，包含同账户认证投影刷新。
- 原计划完整11-spec Chromium矩阵：**37项通过，5.6分钟**，日志 [fix-e2e.log](../../artifacts/product-acceptance/ui-4/self-review/fix-e2e.log)。该轮后补同账户对象刷新分支，以账户ID＋generation消费已确认事实，owner视图仍保持原引用隔离；随后再次跑完整前端门禁，并补 `auth.spec.ts` / `multi-tab.spec.ts` 浏览器复验，其独立结果为 **6项通过（1.0分钟）**，见 [fix-account-e2e.log](../../artifacts/product-acceptance/ui-4/self-review/fix-account-e2e.log)，不把重复用例累加到37项。
- 真实API/持久化6项通过，84.42秒；原实施30项保留历史记录。
- 最终源码SHA：[fix-source-sha256.txt](../../artifacts/product-acceptance/ui-4/self-review/fix-source-sha256.txt)。UI-4截图在本轮浏览器重新生成；UI-2/UI-3历史截图恢复原版本。

本批提交包含原UI-4实现及自检修复，提交主题为 `feat(web): implement UI-4 workspace and fix review findings`；原基线HEAD保持在审查说明中。无后端实现/数据库迁移/新接口变更；没有PR或push。
