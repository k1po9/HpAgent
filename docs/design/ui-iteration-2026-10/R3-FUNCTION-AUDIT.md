# R3 功能清单核查 · 2026-10-14

结论：按用户最终接受的 R3 attempt-03 合同，功能实现清单已覆盖，未发现阻止准备 R4 的功能缺项。R3 保持“已接受”。本次只读核查代码、实现接线、已有测试与验收记录，不重审视觉效果，不重新进行浏览器验收，不修改产品源码。

## 权威与版本

- [最终 DECISION](../../../artifacts/product-acceptance/ui-iteration/R3/attempt-03/DECISION.md)、[冻结合同](../../../artifacts/product-acceptance/ui-iteration/R3/attempt-03/INTERACTION-CONTRACT.md)、[HANDOFF](../../../artifacts/product-acceptance/ui-iteration/R3/attempt-03/HANDOFF.md)。
- 本次用 SHA-256 逐文件核实 [source-manifest](../../../artifacts/product-acceptance/ui-iteration/R3/attempt-03/logs/source-manifest.json)：271 个工作区文件与 271 个冻结副本均一致，0 差异。清单记录指纹 `eb0790f3f20ec49e35d0772ae0173fc2b1b4093d71c4f3b7958dd9c9a08931c0`；未提交状态保留。
- 最终合同优先于初始 R3-PLAN、A/C Skill 中旧安排：接受后 queued/running 均可接力，waiting 圆环、running 菱形，旅行 560ms + 成环 240ms，正常 phase 变化不中断，用户直接 HTML 审核而不要求录像。这些是已批准变更，不是漏实现。

## 功能覆盖

| 要求 | 实现与接线证据 | 核查结论 |
| --- | --- | --- |
| 确认本次发送成功后才绑定新 Run；拒绝/未知不提前出发 | [ChatPane](../../../web/src/components/ChatPane.tsx) 的 handleSend、原提交确认入口；[handoff](../../../web/src/components/input-action/handoff.ts) 的 leaseForSubmission/confirmHandoff | 已实现；未知确认复用原租约，不新增发送 |
| 一次性，关联账户、会话、旧/新 Run、导航与订阅代际 | handoff 的 Ticket/id 与旧回调校验；[ActionHandoff](../../../web/src/components/input-action/ActionHandoff.tsx) 的 eligible、effect cleanup | 已实现；历史恢复没有新租约，不补播 |
| 成功接受后的 queued/running 均可接纳 | ActionHandoff 两种真实 status 的资格判断；控制器不依赖 progress phase | 已实现；不是把 queued 伪装成 running |
| 五锚点渐冷、实际坐标、单条旅行加逆时针约 320° 圆弧 | [geometry](../../../web/src/components/input-action/geometry.ts) 的 orbitGeometry/temperature；ActionHandoff 的 viewBox、path length、getPointAtLength | 已实现；按真实 glyph 半径适配 |
| 先旅行、后成环、捕获后才稳定旋转 | ActionHandoff 560/240ms 分段、同路径弧段推进；[run.css](../../../web/src/styles/run.css) 的 data-handoff 旋转抑制 | 已实现；正常结束清理租约并交还 C |
| waiting 圆环逆时针 6.4s，running 菱形，原颜色不变 | [VitalitySignal](../../../web/src/components/vitality/VitalitySignal.tsx)、run.css、既有 vitality policy；waiting↔running 200ms 特例 | 已实现；compact/detail 使用同一 Glyph 映射 |
| 同 Run 快速进入 thinking/tool 不取消，不重发；业务立即更新 | ActionHandoff 生命周期依赖 Run 身份而非 phase；接纳颜色每帧读取当前事实对应颜色；RunStatus 文本独立 | 已实现；终态仍优先取消 |
| 自动布局有界等待，手动滚动与几何失效可取消 | 出发前稳定 60ms、最长 600ms；wheel/touchmove、出发后 scroll、resize、目标偏移检测 | 已实现；失效降级回当前 C |
| 停止、失败/终态、换账户/路由/订阅、hidden/减动、modal、长帧取消 | eligible、run.status 检查、[共享 preference](../../../web/src/components/motion/preference.ts)、180ms 长帧边界、finish 清理 | 已实现；装饰不持有业务权限 |
| 保留幂等、草稿 revision、IME、停止与流式正文 | ChatPane 继续调用原 sendMessage/confirmPendingSend/stopRun；[HpThread](../../../web/src/adapters/assistant-ui/HpThread.tsx) 原提交与 IME 保护 | 已接入现有业务流程，没有以动画完成代替业务成功 |
| 评审场景不进入产品业务 | 评审 HTML/夹具在 artifacts 与 review scripts；实际运行代码从 Store 读取真实事实 | 交付中明确区分夹具与隔离真实 API/Fake SSE |

## 证据边界与交接事项

[最终 VALIDATION](../../../artifacts/product-acceptance/ui-iteration/R3/attempt-03/VALIDATION.md) 记录 60 项相关单测、生产构建及 11 项浏览器检查。本次读取 browser.json 的 checks/errors，检查条目与报告相符、errors 为空；没有把这些历史结果说成新跑的测试。相关 handoff 测试覆盖租约身份、旧回调、未知确认和路径/渐冷边界。

最终 attempt-03 浏览器范围为桌面 Chrome 1680×1000；其他尺寸是历史证据，真机/其他浏览器/真实付费供应商不属于本次已验证范围。功能清单覆盖不等于所有环境零缺陷。

文档一致性遗留：[FEEDBACK-MATRIX](../../../artifacts/product-acceptance/ui-iteration/R3/attempt-03/FEEDBACK-MATRIX.md) 末行仍是“R3待人审”。以时间更晚且明确记录用户决定的 DECISION/冻结合同为准；这不影响功能覆盖和接受结论，本次不改写历史验收包。

R4 的银丝、到达门控、X/返回分离、柔和关闭尚未实现，属于原定后续范围，不是 R3 缺项。R4 必须保持本次接受后的接力与图标语义，不按旧 Skill 回退。
