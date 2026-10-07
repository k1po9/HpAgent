# UI-3 实施自检

日期：2026-10-07（Asia/Shanghai）。核验 HEAD：`d577c36`（`feat(web): implement UI-3 execution and diagnostics`）；初始工作区干净。

> 修复批次：基于 `d577c36` 已修复 R1/R2 和轮询覆盖缺口，最终验证通过；下方原审查记录保持历史语义，当前修复与验证见末尾。

对照 [实施报告](ui-3-execution-diagnostics-report.md)、[实施计划](ui-3-execution-diagnostics-plan.md)、本次提交及验收日志完成静态审查与定向复现。本轮未修改业务代码，未启动数据库/API/浏览器测试环境。结论：确认两个实现缺陷，修复前不应将相关验收项视作完整通过；既有测试通过记录不作撤销或改写。

## R1 · P2：Run 完成后已发布输出列表不刷新

位置：[RunResources.tsx](../../web/src/components/run/RunResources.tsx#L80)，关联 U3-14/U3-17。

复现：运行中进入“使用资料与输出”，初次 published-files 返回空；保持该页签打开，Run 变为 succeeded，服务端此时已有输出。组件只在 runId 挂载时调用 outputs，terminal 变化只更新说明文案，不重新获取输出，继续显示“暂无已发布输出”。上方“同步执行”只同步 snapshot，“刷新资料”只同步 candidates；输出成功为空时也没有输出刷新按钮。用户需切换页签或关闭重开才能看到结果。

定向测试将 terminal=false 更新为 true，并让后续输出 GET 返回 result.txt：预期再次请求并显示下载链接，实际请求次数仍为 1，断言失败。

修复要求：同 Run 进入终态时刷新输出；提供明确的输出手动刷新入口。可见非终态是否限频更新由查询策略统一决定，避免为文件行创建轮询；保留 outputToken、卸载保护与网络失败旧数据。补“运行中空列表→完成有输出”“迟到旧列表不能覆盖新结果”“刷新失败可恢复”测试。

## R2 · P1：明确权限拒绝后仍保留其他 Model Input 正文

位置：[traceStore.ts](../../web/src/components/trace/traceStore.ts#L264)，关联 U3-07/U3-11。

复现：同一 Run 已加载一份 full_safe 正文，另一份详情请求在途；账户权限降低后，第三份详情 GET 返回真实接口定义的 `403 model_input_unavailable`。catch 只把第三份记录设为 unavailable，没有递增 modelGeneration 或清理同会话其他正文。之前已加载的正文仍可见，先前发起的 full_safe 请求随后返回也会被接纳。

定向测试确认：拒绝之后仍存在 **2 份** provider_request_body，预期为 0。不是“没有权限变化推送”的已知限制，因为服务端已明确返回权限不可用；报告中“权限拒绝清正文并递增模型查询世代”的描述未覆盖这条 catch 分支。

修复要求：将账户级 `model_input_unavailable` 作为模型正文缓存的失效边界，递增 modelGeneration、清除相关已加载内容并使在途旧响应失效，保留可理解的不可用反馈。普通单 snapshot 404 应区分对象缺失与账户级权限撤回，不能一概假定全局权限变化。补“已加载+在途+明确权限拒绝”的组合测试，并验证 summary/full_safe 恢复时仅经新请求重新显示。复跑原会话隔离与 Model Input 测试。

## 验收证据补充项

报告 U3-17 列出了可见非终态轮询、隐藏/关闭清定时器与懒加载。当前 RunInspector 组件测试只有 Work 独立加载/返回导航、概览不查诊断两项；相关新测试和 UI-3 E2E 中未找到 visibilitychange、假时钟推进、终态停止轮询的直接断言。代码存在相应分支，不能据此当作这些时序均已由测试证明。

修复批次应补明确的轮询生命周期测试：可见非终态递归查询且不重叠、隐藏暂停及恢复、终态停止周期请求、关闭后迟到响应/定时器不再生效，并更新 U3-17 的具体证据。此项属于覆盖缺口，不单凭缺测试推断功能一定错误。

## 本轮验证记录

| 批次 | 结果 | 证据 |
| --- | --- | --- |
| 既有定向 Vitest：RunInspector、RunResources、store/runInspector、traceStore | 4 文件、21 项通过；exit 0 | [existing-targeted.log](../../artifacts/product-acceptance/ui-3/self-review/existing-targeted.log) |
| 新增缺陷复现：R1/R2 | 1 文件、2 项失败；exit 1，均为预期行为断言失败 | [repro.log](../../artifacts/product-acceptance/ui-3/self-review/repro.log) |
| 复现源码 | 合成数据，保留为文本，不加入默认测试集合 | [ui3-review-repro.test.tsx.txt](../../artifacts/product-acceptance/ui-3/self-review/ui3-review-repro.test.tsx.txt) |

首次写临时复现文件时误用相对路径，文件未创建；随后先完成既有 4 文件的 21 项测试，再修正路径单独执行两个复现。没有将未执行的复现计入通过数量。

既有批次在 web/ 执行：

```bash
npx vitest run src/components/run/RunInspector.test.tsx src/components/run/RunResources.test.tsx src/store/runInspector.test.ts src/components/trace/traceStore.test.ts --maxWorkers=1
```

复现批次在 web/src/components/run/ui3-review-repro.test.tsx 临时放置上述源码后，在 web/ 执行：

```bash
npx vitest run src/components/run/ui3-review-repro.test.tsx --maxWorkers=1
```

复现后临时测试已移入证据目录为 .txt，业务实现和正式测试保持原状。本轮未重跑全量 197 项、TypeScript/lint/build、28 项浏览器或后端 45 项；这些仍是原报告的历史证据。后续修复需以最终源码重新记录相关批次，不累计重叠用例。

## 修复与补验（2026-10-07）

- **R1 已修复**：输出查询独立监听 runId/terminal，进入终态刷新；增加始终可见的“刷新输出”。切换状态/卸载使旧 outputToken 失效，旧空列表不能覆盖新结果；网络错误保留旧文件并可重试。没有新增文件行轮询。
- **R2 已修复**：确认 `403 model_input_unavailable` 是后端的账户 visibility 边界；该分支递增 modelGeneration 并清除其他 Model Input 缓存，仅保留被拒绝项的 unavailable 提示。此前在途正文不能回灌；summary/full_safe 恢复都通过新的详情请求获取。普通 snapshot 404 只影响该记录，保留其他已加载内容和请求。
- **U3-17 补验**：RunInspector 假时钟直接断言 snapshot 可见非终态递归且不重叠、隐藏暂停/恢复、终态停止、失败退避以及关闭后的 abort/迟到响应/定时器清理；另直接断言 Trace 与 pending 审批的隐藏、恢复、终态和关闭行为。
- **补验发现并修复 R3**：Trace 周期 GET 在途时隐藏再恢复，原逻辑会额外创建一个定时链。假时钟复现为预期 2 次请求、实际 3 次；增加本地 inFlight 保护，并让终态退出周期 effect、隐藏时不再续订计时器。保留高级页首次按需查询。修复前证据：[polling-before-fix.log](../../artifacts/product-acceptance/ui-3/self-review/polling-before-fix.log)。
- **浏览器补验**：增加明确标注的网络夹具场景，覆盖保持资料页打开时“运行中空输出→终态 result.txt→手动刷新”，以及“已加载正文+在途正文+明确 403→无旧正文→新请求恢复 summary”。仅使用合成数据，不证明真实文件发布或真实供应商请求。

最终全量 Vitest **208/208（30 个文件）通过，exit 0**；定向会话/Model Input/Run 相关 **42/42（6 个文件）通过，exit 0**。TypeScript、ESLint/Prettier、生产构建均 **exit 0**；UI-3 Chromium **3/3 通过，exit 0**。构建仍有主 bundle >500 kB 的既有提示。完整日志和两张新增合成截图见 [修复证据目录](../../artifacts/product-acceptance/ui-3/self-review/README.md)。原 197 项单测、28 项浏览器及 45 项后端契约属于 `d577c36` 历史证据；本批未修改后端接口或状态机，不重新累计这些批次。
