# UI-7 自检修复记录

> 2026-10-08（Asia/Shanghai）。基线 HEAD：`e6791e35990ce6da811f6204806def202e770076` 上的未提交 UI-7 实现。
> 状态：R1/R2/R3 均已修复，最终独立复验通过。原缺陷及修复前证据见[独立自检](ui-7-self-review.md)。

## 修复结果

| 项目 | 修复 | 正式回归 |
| --- | --- | --- |
| R1：迟到 Run 查询抢走新前景 | RunLookup 订阅 Shell 的 modal/sidebar/待确认交互变化使表单导航 token 失效；Surface 记录新开/激活 modal 的纯 UI 交互世代，覆盖父对象内的局部子弹窗。保留账户、导航和请求保护；不增加领域 requestToken，不恢复导航 bridge。仅响应式模式变化不视为新操作。 | RunLookup.test.tsx：账户/资料、Sidebar 与 modal 开关 A→B→A、父对象局部子弹窗开/关、响应式切换及正常打开；浏览器 Header 与局部父层场景。 |
| R2：summary 无法 Tab 到达 | 保留 native dialog 的 top-layer/inert 隔离及 Escape；只在首尾边界执行键盘回绕。共享 tabOrder 按原生可 Tab 条件和正 tabindex 顺序枚举 summary、iframe、表单/链接/可编辑元素，排除负 tabindex、hidden/inert、closed details 内部、disabled/fieldset 等；保留 disabled fieldset 首个 legend 的原生例外与 radio 分组。 | 原始 R2 反例归入 Surface.test.tsx；Chromium 通知末尾 summary Tab/Shift+Tab/Enter/Space；公共 Surface 开头/末尾 details、iframe 内外、正 tabindex、fieldset legend、父子 modal 和桌面背景路径。 |
| R3：普通聊天终态额外加载 Trace | Trace store 新增 refreshSelectedRun，仅在 selectRun 建立的诊断生命周期且 Run 匹配时同步；followRun 不赋予诊断查询资格。Workbench 终态继续 reconcile 最后消息并执行权威 Run GET，Trace 刷新走独立 action；历史选择、缓冲、generation/权限清理保持。 | workbench.trace.test.ts：普通/概览/当前高级/历史高级/关闭/账户 reset；traceStore 在途关闭清理；Chromium 合成 SSE 下 5 种诊断状态的 Trace 请求差量与权威最后消息。 |

R2 先尝试完全使用 native Tab 行为，扩展 Chromium 反例发现末尾焦点可能离开内容区，故最终保留完整语义集合的边界回绕。未通过给通知 summary 单独加 tabindex 绕过共享 primitive。浏览器 fixture 中单独核实 iframe 输入的 Tab/Shift+Tab，而不是只断言 iframe DOM 存在。

R1 迟到成功仍可正常完成 GET，但旧自动导航意图失效；输入保留，用户可重新查询。Surface 的交互计数与领域请求/持久状态无关，StrictMode 重放、窗口 resize、扩大模式变化不伪造新导航操作。

## 独立验证批次

证据入口：[self-review-fixes](../../artifacts/product-acceptance/ui-7/self-review-fixes/README.md)。原独立自检 87 项和原实施 391/62 项保留为历史，不相加为修复批次结果。

- 原始三项临时反例在本轮修复前再跑：3 失败，exit 1；实施修复后相同源文件再跑：3 通过，exit 0。随后移入正式组件/Store 回归，临时文件移除。
- 扩展 fixture Chromium：12 项通过，使用当前 Vite 与 API 响应 fixture/合成终态；无真实业务变化。首次扩展中的定位器、消息匹配与初始 StrictMode 请求数假设修正有独立失败日志，键盘边界问题按真实浏览器结果修复。
- 定向 Store 参数化初轮受 Zustand 状态快照中遗留 spy 影响，测试清理显式恢复原 loadTrace 函数，6 项复验通过；这属于测试隔离修正，不改产品逻辑或断言。
- 最终 typecheck / lint / build 均 exit 0，Lint 无警告；完整 Vitest **54 文件 / 405 项通过**；综合 Chromium **74 / 74 通过，exit 0（15.0 分钟）**，包含 12 个新 fixture 修复场景和原 62 项受影响场景，不相加。新追加的开头 details / iframe 内外双向 Tab 路径及响应式查询保护均在最终批次通过。

新增浏览器 spec 可用 `npx playwright test --config=playwright.ui7-review.config.ts` 单独运行，仅起 Vite 5288、不需要后端。`e2e/ui7-surface-harness.html/tsx` 是 Vite 测试夹具，不接入产品路由或 production 构建入口。综合批次继续用正常 Playwright 配置、真实 API 与测试执行器，使用新库 `hpagent_ui7_review_20261008`、Redis DB11、API8187/Vite5280、新文件目录 `/tmp/hpagent-ui7-review-files-20261008`；默认协调容量保持。

## 验收与证据边界

A7-04 的迟到前景保护、A7-05 的诊断懒加载、A7-09/A7-19 的 summary/iframe 键盘路径，以及 A7-21 的普通聊天 Trace GET 已补正式回归。A7-19/A7-21 仍是部分覆盖：这些键盘与请求差量结果不能推导实际读屏或全量资源峰值通过。真机软键盘/非零安全区、全产品 200% 文本、全站色对、真实模型/Temporal/渠道的原边界继续保留。

原 148 张实施截图及独立自检文件保持，修复批次的新截图/记录/源码指纹单独归档。旧 spec 写入历史固定路径后，将本轮文件复制到修复证据，再恢复原内容。未部署、未创建 commit/PR、未修改后端模型/协议、未清理业务库。

64 项前端文件状态/指纹及相对独立自检基线的 14 项修复文件见 source-fingerprints.json / repair-files.json；最终结束时复核一致。原实施 148 项与独立自检 16 项文件的内容哈希再次通过。API8187 / Vite5280 / fixture Vite5288 均停止，无残留监听；专用测试库保留供调查，业务服务未停止。
